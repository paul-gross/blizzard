"""SQLAlchemy adapter for the flows seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only (``bzh:facts-not-status``): a
submission is open while it has no closure, a turn while it has no ``ended_at``; nothing here stores either. Every write
opens on the tenant row's lock (``ScopedConnections.write``), which serializes the tenant's arrivals and closures; the
unique keys are the backstop, not the guard."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Row, func

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.delivery.flows import (
    TURN_INTERRUPTED,
    Flow,
    FlowFact,
    FlowLanding,
    FlowResource,
    LandingWrite,
    Submission,
    SubmissionClosure,
    SubmissionRecord,
    Turn,
    TurnEvent,
    VisitStanding,
    Withdrawal,
)
from blizzard.hub.domain.delivery.ports.flows import IWriteFlows
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_rows import conn_of, lock_keys, write_landing_rows
from blizzard.hub.store.internal.flow_rows import (
    close_submission,
    end_turn,
    flow_of,
    flows_held,
    has_open_turn,
    write_event,
    write_facts,
    write_landing,
)
from blizzard.hub.store.scoped import ScopedConnections

_sub = s.flow_submissions
_held = s.flow_submission_flows
_closures = s.flow_submission_closures
_turns = s.hub_turns
#: The ``keyed_locks`` namespace a turn takes its flows' resources under.
_FLOW_LOCK_NAMESPACE = "flow"


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

    def submissions_of(self, chunk_id: str) -> list[SubmissionRecord]:
        with self._store.read("submissions_of") as conn:
            joined = self._store.scope_join(
                _sub, _closures, _closures.c.submission_id == _sub.c.submission_id, isouter=True
            )
            rows = conn.execute(
                self._store.select(joined, *_sub.c, _closures.c.outcome, _closures.c.closed_at).where(
                    _sub.c.chunk_id == chunk_id
                )
            ).all()
            # Sorted here, not in SQL: an ORDER BY arrival steers the planner onto the tenant-wide arrival index,
            # where the chunk's own visit index bounds the read to this chunk's rows.
            rows = sorted(rows, key=lambda r: r.arrival)
            return [
                SubmissionRecord(
                    submission_id=r.submission_id,
                    node_id=r.node_id,
                    epoch=r.epoch,
                    generation=r.generation,
                    arrival=r.arrival,
                    submitted_at=r.submitted_at,
                    closure=None
                    if r.closed_at is None
                    else SubmissionClosure(outcome=r.outcome, closed_at=r.closed_at),
                )
                for r in rows
            ]

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

    def turn_outcomes(self, chunk_id: str, *, node_id: str, epoch: int) -> list[str]:
        with self._store.read("turn_outcomes") as conn:
            joined = self._store.scope_join(_turns, _sub, _turns.c.submission_id == _sub.c.submission_id)
            return list(
                conn.execute(
                    self._store.select(joined, _turns.c.outcome)
                    .where(
                        _sub.c.chunk_id == chunk_id,
                        _sub.c.node_id == node_id,
                        _sub.c.epoch == epoch,
                        _turns.c.ended_at.is_not(None),
                    )
                    .order_by(_turns.c.ended_at.desc(), _turns.c.turn_id.desc())
                ).scalars()
            )

    def visit_standing(self, chunk_id: str, *, node_id: str, epoch: int) -> VisitStanding | None:
        submission = self.submission_for_visit(chunk_id, node_id=node_id, epoch=epoch)
        if submission is None or submission.closed is not None:
            return None
        with self._store.read("visit_standing") as conn:
            joined = self._store.scope_join(_held, _sub, _held.c.submission_id == _sub.c.submission_id)
            ahead = tuple(
                int(
                    conn.execute(
                        self._store.select(joined, func.count()).where(
                            _held.c.flow_id == flow.flow_id,
                            _sub.c.arrival < submission.arrival,
                            ~self._store.exists(_closures, _closures.c.submission_id == _sub.c.submission_id),
                        )
                    ).scalar()
                    or 0
                )
                for flow in submission.flows
            )
            return VisitStanding(
                submission=submission, turning=has_open_turn(conn, submission.submission_id), ahead=ahead
            )

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
            write_facts(conn, flows, submission_id, chunk_id, kind="submitted", outcome=None, at=at)
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
            if has_open_turn(conn, submission_id):
                return False
            return close_submission(conn, submission_id, outcome=outcome, at=at)

    def take_turn(self, submission_id: str, *, passed_over: frozenset[str], at: datetime) -> Turn | None:
        with self._store.write("take_turn") as conn:
            row = conn.execute(self._store.select(_sub).where(_sub.c.submission_id == submission_id)).first()
            if row is None:
                return None
            flows = flows_held(conn, [submission_id]).get(submission_id, ())
            lock_keys(conn, _FLOW_LOCK_NAMESPACE, [flow.resource for flow in flows])
            closed = conn.execute(
                self._store.select(_closures, _closures.c.submission_id).where(
                    _closures.c.submission_id == submission_id
                )
            ).first()
            if closed is not None or has_open_turn(conn, submission_id):
                return None
            if flows and (
                self._busy(conn, flows) or not self._ahead_on(conn, flows, arrival=row.arrival) <= passed_over
            ):
                return None
            turn = Turn(
                turn_id=Id.mint(IdPrefix.HUB_TURN, self._clock).value, submission_id=submission_id, started_at=at
            )
            conn.execute(
                self._store.insert(_turns).values(turn_id=turn.turn_id, submission_id=submission_id, started_at=at)
            )
            write_facts(conn, flows, submission_id, row.chunk_id, kind="turn-started", outcome=None, at=at)
            return turn

    def end_turn_locked(
        self,
        handle: ILockedChunkRead,
        turn_id: str,
        *,
        outcome: str,
        closure: str | None,
        event: TurnEvent | None,
        at: datetime,
    ) -> bool:
        conn = conn_of(handle)
        submission_id = end_turn(conn, turn_id, outcome=outcome, at=at)
        if submission_id is None:
            return False
        if event is not None:
            write_event(conn, event, at=at)
        if closure is not None:
            close_submission(conn, submission_id, outcome=closure, at=at)
        return True

    def record_landing(
        self,
        landing: FlowLanding,
        *,
        node_id: str,
        node_name: str,
        epoch: int,
        admission: EpochAdmission,
        repo: str,
        revision: str,
        at: datetime,
    ) -> LandingWrite:
        with self._store.write("record_flow_landing") as conn:
            refused, wrote = write_landing_rows(
                conn,
                landing.chunk_id,
                node_id=node_id,
                node_name=node_name,
                epoch=epoch,
                admission=admission,
                repo=repo,
                revision=revision,
                repository_id=landing.flow.repository_id,
                artifact_id=Id.mint(IdPrefix.ARTIFACT, self._clock).value,
                at=at,
            )
            event_id = write_landing(conn, landing, revision=revision, at=at) if wrote else None
            return LandingWrite(refused=refused, event_id=event_id)

    def end_turn_interrupted(
        self, turn_id: str, *, withdrawal: Withdrawal | None, event: TurnEvent, at: datetime
    ) -> bool:
        with self._store.write("end_turn_interrupted") as conn:
            submission_id = end_turn(conn, turn_id, outcome=TURN_INTERRUPTED, at=at)
            if submission_id is None:
                return False
            write_event(conn, event, at=at)
            if withdrawal is not None:
                close_submission(conn, submission_id, outcome=withdrawal, at=at)
            return True

    def _busy(self, conn: Connection, flows: Sequence[Flow]) -> bool:
        """Whether any of ``flows`` has an open turn — of any submission holding it, open or closed."""
        joined = self._store.scope_join(_turns, _held, _turns.c.submission_id == _held.c.submission_id)
        return (
            conn.execute(
                self._store.select(joined, _turns.c.turn_id)
                .where(_held.c.flow_id.in_([flow.flow_id for flow in flows]), _turns.c.ended_at.is_(None))
                .limit(1)
            ).first()
            is not None
        )

    def _ahead_on(self, conn: Connection, flows: Sequence[Flow], *, arrival: int) -> set[str]:
        """The open submissions on any of ``flows`` that arrived before ``arrival``."""
        joined = self._store.scope_join(_held, _sub, _held.c.submission_id == _sub.c.submission_id)
        return set(
            conn.execute(
                self._store.select(joined, _sub.c.submission_id).where(
                    _held.c.flow_id.in_([flow.flow_id for flow in flows]),
                    _sub.c.arrival < arrival,
                    ~self._store.exists(_closures, _closures.c.submission_id == _sub.c.submission_id),
                )
            ).scalars()
        )

    def _flow_for(self, conn: Connection, resource: FlowResource, *, at: datetime) -> Flow:
        """The tenant's flow on ``resource``, made now when none is."""
        row = conn.execute(self._store.select(s.flows).where(s.flows.c.resource == resource.resource)).first()
        if row is not None:
            return flow_of(row)
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

    def _submissions(
        self, conn: Connection, rows: Sequence[Row[Any]], *, closed: dict[str, str | None]
    ) -> list[Submission]:
        held = flows_held(conn, [r.submission_id for r in rows])
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


_flows_conforms: type[IWriteFlows] = FlowStore
