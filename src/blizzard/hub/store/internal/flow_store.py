"""SQLAlchemy adapter for the flows seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only (``bzh:facts-not-status``): a
submission is open while it has no closure, a turn while it has no ``ended_at``; nothing here stores either. Every write
opens on the tenant row's lock (``ScopedConnections.write``), which serializes the tenant's arrivals and closures; the
unique keys are the backstop, not the guard."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Row, func

from blizzard.foundation.clock import IClock
from blizzard.foundation.event_log import EVENT_LOG_SEVERITY
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.delivery.flows import (
    TURN_INTERRUPTED,
    Flow,
    FlowFact,
    FlowFactKind,
    FlowResource,
    Submission,
    Turn,
    TurnEvent,
    Withdrawal,
)
from blizzard.hub.store import schema as s
from blizzard.hub.store.scoped import ScopedConnections

_sub = s.flow_submissions
_held = s.flow_submission_flows
_closures = s.flow_submission_closures


class FlowStore:
    """Each branch's delivery line: submissions to it, their closures, turns, and the flows' facts."""

    def __init__(self, store: ScopedConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def open_submissions_from(self, floor: int) -> list[Submission]:
        with self._store.read("open_submissions_from") as conn:
            rows = conn.execute(
                self._store.select(_sub)
                .where(
                    _sub.c.arrival >= floor,
                    ~self._store.exists(_closures, _closures.c.submission_id == _sub.c.submission_id),
                )
                .order_by(_sub.c.arrival)
            ).all()
            return self._submissions(conn, rows, closed={})

    def open_turns(self) -> list[Turn]:
        with self._store.read("open_turns") as conn:
            rows = conn.execute(
                self._store.select(s.hub_turns).where(s.hub_turns.c.ended_at.is_(None)).order_by(s.hub_turns.c.turn_id)
            ).all()
            return [
                Turn(
                    turn_id=r.turn_id,
                    submission_id=r.submission_id,
                    started_at=r.started_at,
                    ended_at=r.ended_at,
                    outcome=r.outcome,
                )
                for r in rows
            ]

    def submission_for_visit(self, chunk_id: str, *, node_id: str, epoch: int) -> Submission | None:
        with self._store.read("submission_for_visit") as conn:
            row = conn.execute(
                self._store.select(_sub)
                .where(_sub.c.chunk_id == chunk_id, _sub.c.node_id == node_id, _sub.c.epoch == epoch)
                .order_by(_sub.c.generation.desc())
                .limit(1)
            ).first()
            if row is None:
                return None
            outcome = conn.execute(
                self._store.select(_closures, _closures.c.outcome).where(_closures.c.submission_id == row.submission_id)
            ).scalar()
            return self._submissions(conn, [row], closed={row.submission_id: outcome})[0]

    def facts_of(self, submission_id: str) -> list[FlowFact]:
        with self._store.read("facts_of") as conn:
            rows = conn.execute(
                self._store.select(s.flow_facts)
                .where(s.flow_facts.c.submission_id == submission_id)
                .order_by(s.flow_facts.c.id)
            ).all()
            return [
                FlowFact(
                    flow_id=r.flow_id,
                    submission_id=r.submission_id,
                    chunk_id=r.chunk_id,
                    kind=r.kind,
                    at=r.at,
                    outcome=r.outcome,
                    revision=r.revision,
                )
                for r in rows
            ]

    def submit(
        self,
        chunk_id: str,
        *,
        node_id: str,
        epoch: int,
        generation: int,
        resources: Sequence[FlowResource],
        at: datetime,
    ) -> Submission | None:
        with self._store.write("submit_flow_visit") as conn:
            already = conn.execute(
                self._store.select(_sub, _sub.c.submission_id).where(
                    _sub.c.chunk_id == chunk_id,
                    _sub.c.node_id == node_id,
                    _sub.c.epoch == epoch,
                    _sub.c.generation == generation,
                )
            ).first()
            if already is not None:
                return None
            # The tenant's newest plus one, read under the tenant row lock the write opened on.
            arrival = (conn.execute(self._store.select(_sub, func.max(_sub.c.arrival))).scalar() or 0) + 1
            submission_id = Id.mint(IdPrefix.FLOW_SUBMISSION, self._clock).value
            conn.execute(
                self._store.insert(_sub).values(
                    submission_id=submission_id,
                    chunk_id=chunk_id,
                    node_id=node_id,
                    epoch=epoch,
                    generation=generation,
                    arrival=arrival,
                    submitted_at=at,
                )
            )
            flows = tuple(self._flow_for(conn, resource, at=at) for resource in dict.fromkeys(resources))
            for flow in flows:
                conn.execute(self._store.insert(_held).values(submission_id=submission_id, flow_id=flow.flow_id))
            self._write_facts(conn, flows, submission_id, chunk_id, kind="submitted", outcome=None, at=at)
            return Submission(
                submission_id=submission_id,
                chunk_id=chunk_id,
                node_id=node_id,
                epoch=epoch,
                generation=generation,
                arrival=arrival,
                submitted_at=at,
                flows=flows,
            )

    def close(self, submission_id: str, *, outcome: Withdrawal, at: datetime) -> bool:
        with self._store.write("close_flow_submission") as conn:
            turn_open = conn.execute(
                self._store.select(s.hub_turns, s.hub_turns.c.turn_id).where(
                    s.hub_turns.c.submission_id == submission_id, s.hub_turns.c.ended_at.is_(None)
                )
            ).first()
            if turn_open is not None:
                return False
            return self._close(conn, submission_id, outcome=outcome, at=at)

    def end_turn_interrupted(
        self, turn_id: str, *, withdrawal: Withdrawal | None, event: TurnEvent, at: datetime
    ) -> bool:
        with self._store.write("end_turn_interrupted") as conn:
            turn = conn.execute(
                self._store.select(s.hub_turns, s.hub_turns.c.submission_id).where(
                    s.hub_turns.c.turn_id == turn_id, s.hub_turns.c.ended_at.is_(None)
                )
            ).first()
            if turn is None:
                return False
            conn.execute(
                self._store.update(s.hub_turns)
                .where(s.hub_turns.c.turn_id == turn_id)
                .values(ended_at=at, outcome=TURN_INTERRUPTED)
            )
            chunk_id, flows = self._held_by(conn, turn.submission_id)
            self._write_facts(
                conn, flows, turn.submission_id, chunk_id, kind="turn-ended", outcome=TURN_INTERRUPTED, at=at
            )
            conn.execute(
                self._store.insert(s.event_log).values(
                    severity=EVENT_LOG_SEVERITY[event.kind],
                    kind=event.kind,
                    runner_id=None,
                    chunk_id=event.chunk_id,
                    lease_id=None,
                    node_name=event.node_name,
                    message=event.message,
                    detail=json.dumps(dict(event.detail)),
                    recorded_at=at,
                )
            )
            if withdrawal is not None:
                self._close(conn, turn.submission_id, outcome=withdrawal, at=at)
            return True

    def _close(self, conn: Connection, submission_id: str, *, outcome: Withdrawal, at: datetime) -> bool:
        """Insert the closure and its ``withdrawn`` facts only when no closure exists — the first writer wins."""
        closed = conn.execute(
            self._store.select(_closures, _closures.c.submission_id).where(_closures.c.submission_id == submission_id)
        ).first()
        if closed is not None:
            return False
        conn.execute(self._store.insert(_closures).values(submission_id=submission_id, outcome=outcome, closed_at=at))
        chunk_id, flows = self._held_by(conn, submission_id)
        self._write_facts(conn, flows, submission_id, chunk_id, kind="withdrawn", outcome=outcome, at=at)
        return True

    def _flow_for(self, conn: Connection, resource: FlowResource, *, at: datetime) -> Flow:
        """The tenant's flow on ``resource``, made now when none is."""
        row = conn.execute(self._store.select(s.flows).where(s.flows.c.resource == resource.resource)).first()
        if row is not None:
            return _flow(row)
        flow = Flow(
            flow_id=Id.mint(IdPrefix.FLOW, self._clock).value,
            repository_id=resource.repository_id,
            branch=resource.branch,
            created_at=at,
        )
        conn.execute(
            self._store.insert(s.flows).values(
                flow_id=flow.flow_id,
                resource=flow.resource,
                repository_id=flow.repository_id,
                branch=flow.branch,
                created_at=at,
            )
        )
        return flow

    def _held_by(self, conn: Connection, submission_id: str) -> tuple[str, tuple[Flow, ...]]:
        """The submission's chunk and the flows it holds."""
        chunk_id = conn.execute(
            self._store.select(_sub, _sub.c.chunk_id).where(_sub.c.submission_id == submission_id)
        ).scalar_one()
        return chunk_id, self._flows_held(conn, [submission_id]).get(submission_id, ())

    def _flows_held(self, conn: Connection, submission_ids: Sequence[str]) -> dict[str, tuple[Flow, ...]]:
        held: dict[str, list[Flow]] = {}
        joined = self._store.scope_join(_held, s.flows, _held.c.flow_id == s.flows.c.flow_id)
        for batch in id_batches(list(submission_ids)):
            rows = conn.execute(
                self._store.select(joined, _held.c.submission_id, *s.flows.c)
                .where(_held.c.submission_id.in_(batch))
                .order_by(_held.c.submission_id, s.flows.c.resource)
            ).all()
            for row in rows:
                held.setdefault(row.submission_id, []).append(_flow(row))
        return {submission_id: tuple(flows) for submission_id, flows in held.items()}

    def _submissions(
        self, conn: Connection, rows: Sequence[Row[Any]], *, closed: dict[str, str | None]
    ) -> list[Submission]:
        held = self._flows_held(conn, [r.submission_id for r in rows])
        return [
            Submission(
                submission_id=r.submission_id,
                chunk_id=r.chunk_id,
                node_id=r.node_id,
                epoch=r.epoch,
                generation=r.generation,
                arrival=r.arrival,
                submitted_at=r.submitted_at,
                flows=held.get(r.submission_id, ()),
                closed=closed.get(r.submission_id),
            )
            for r in rows
        ]

    def _write_facts(
        self,
        conn: Connection,
        flows: Sequence[Flow],
        submission_id: str,
        chunk_id: str,
        *,
        kind: FlowFactKind,
        outcome: str | None,
        at: datetime,
    ) -> None:
        """One fact per flow, so each flow's record stands alone."""
        for flow in flows:
            conn.execute(
                self._store.insert(s.flow_facts).values(
                    flow_id=flow.flow_id,
                    submission_id=submission_id,
                    chunk_id=chunk_id,
                    kind=kind,
                    outcome=outcome,
                    at=at,
                )
            )


def _flow(row: Row[Any]) -> Flow:
    return Flow(flow_id=row.flow_id, repository_id=row.repository_id, branch=row.branch, created_at=row.created_at)
