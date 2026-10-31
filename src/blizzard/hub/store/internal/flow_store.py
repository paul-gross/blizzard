"""SQLAlchemy adapter for the flows seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only (``bzh:facts-not-status``): a
submission is open while it has no closure, a turn while it has no ``ended_at``; nothing here stores either. Every write
opens on the tenant row's lock (``ScopedConnections.write``), which serializes the tenant's arrivals and closures; the
unique keys are the backstop, not the guard."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Connection, FromClause, Row, and_, func

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.chunk.flow_writes import FlowFactWritten
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.delivery.flows import (
    NOT_VERDICTS,
    TURN_INTERRUPTED,
    Flow,
    FlowFact,
    FlowLanding,
    FlowOverview,
    FlowResource,
    LandingTerms,
    LandingWrite,
    Submission,
    SubmissionClosure,
    SubmissionRecord,
    Turn,
    TurnEvent,
    VisitStanding,
    Withdrawal,
)
from blizzard.hub.domain.delivery.ports.flows import IReadFlowViews, IWriteFlows
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_rows import conn_of, lock_keys, write_landing_rows
from blizzard.hub.store.internal.flow_rows import (
    close_submission,
    end_turn,
    fact_of,
    flow_of,
    flows_held,
    has_open_turn,
    write_event,
    write_facts,
    write_landing,
)
from blizzard.hub.store.internal.newest_fact import newest_fact_select
from blizzard.hub.store.scoped import ScopedConnections

_sub = s.flow_submissions
_held = s.flow_submission_flows
_closures = s.flow_submission_closures
_turns = s.hub_turns
_facts = s.flow_facts
_terms = s.flow_submission_terms
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
            return [fact_of(r) for r in rows]

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

    def flow_overviews(self) -> list[FlowOverview]:
        with self._store.read("flow_overviews") as conn:
            rows = conn.execute(self._store.select(s.flows).order_by(s.flows.c.resource)).all()
            return self._overviews(conn, rows)

    def flow_overview(self, flow_id: str) -> FlowOverview | None:
        with self._store.read("flow_overview") as conn:
            rows = conn.execute(self._store.select(s.flows).where(s.flows.c.flow_id == flow_id)).all()
            overviews = self._overviews(conn, rows)
            return overviews[0] if overviews else None

    def record_page(self, flow_id: str, *, before: int | None, limit: int) -> list[FlowFact]:
        with self._store.read("flow_record_page") as conn:
            query = self._store.select(_facts).where(_facts.c.flow_id == flow_id)
            if before is not None:
                query = query.where(_facts.c.id < before)
            return [fact_of(r) for r in conn.execute(query.order_by(_facts.c.id.desc()).limit(limit)).all()]

    def open_submissions_on(self, flow_ids: Sequence[str]) -> list[Submission]:
        with self._store.read("open_submissions_on") as conn:
            joined = self._store.scope_join(_sub, _held, _held.c.submission_id == _sub.c.submission_id)
            rows: dict[str, Row[Any]] = {}
            for batch in id_batches(sorted(set(flow_ids))):
                for row in conn.execute(
                    self._store.select(joined, *_sub.c).where(_held.c.flow_id.in_(batch), self._open())
                ).all():
                    rows.setdefault(row.submission_id, row)
            return self._submissions(conn, sorted(rows.values(), key=lambda r: r.arrival), closed={})

    def open_submissions_of(self, chunk_ids: Sequence[str]) -> list[Submission]:
        with self._store.read("open_submissions_of") as conn:
            rows = [
                row
                for batch in id_batches(sorted(set(chunk_ids)))
                for row in conn.execute(self._store.select(_sub).where(_sub.c.chunk_id.in_(batch), self._open())).all()
            ]
            return self._submissions(conn, sorted(rows, key=lambda r: r.arrival), closed={})

    def chunk_records(self, chunk_ids: Sequence[str]) -> dict[str, list[FlowFact]]:
        with self._store.read("chunk_flow_records") as conn:
            submission_ids = [
                submission_id
                for batch in id_batches(sorted(set(chunk_ids)))
                for submission_id in conn.execute(
                    self._store.select(_sub, _sub.c.submission_id).where(_sub.c.chunk_id.in_(batch))
                ).scalars()
            ]
            facts = [
                fact_of(row)
                for batch in id_batches(submission_ids)
                for row in conn.execute(self._store.select(_facts).where(_facts.c.submission_id.in_(batch))).all()
            ]
            records: dict[str, list[FlowFact]] = {}
            for fact in sorted(facts, key=lambda f: f.id):
                records.setdefault(fact.chunk_id, []).append(fact)
            return records

    def terms_of(self, submission_id: str) -> dict[str, LandingTerms]:
        with self._store.read("terms_of") as conn:
            rows = conn.execute(
                self._store.select(_terms)
                .where(_terms.c.submission_id == submission_id)
                .order_by(_terms.c.repository_id)
            ).all()
            return {r.repository_id: _terms_of(r) for r in rows}

    def first_turn_at(self, chunk_id: str, *, node_id: str, epoch: int) -> datetime | None:
        with self._store.read("first_turn_at") as conn:
            submissions = list(
                conn.execute(
                    self._store.select(_sub, _sub.c.submission_id).where(
                        _sub.c.chunk_id == chunk_id, _sub.c.node_id == node_id, _sub.c.epoch == epoch
                    )
                ).scalars()
            )
            if not submissions:
                return None
            started: datetime | None = conn.execute(
                self._store.select(_turns, func.min(_turns.c.started_at)).where(_turns.c.submission_id.in_(submissions))
            ).scalar()
            return started

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

    def close(self, submission_id: str, *, outcome: Withdrawal, at: datetime) -> tuple[FlowFactWritten, ...] | None:
        with self._store.write("close_flow_submission") as conn:
            if has_open_turn(conn, submission_id):
                return None
            return close_submission(conn, submission_id, outcome=outcome, at=at)

    def take_turn(
        self,
        submission_id: str,
        *,
        passed_over: frozenset[str],
        terms: Sequence[LandingTerms],
        at: datetime,
    ) -> Turn | None:
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
            self._take_terms(conn, row, flows, terms)
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
    ) -> tuple[FlowFactWritten, ...] | None:
        conn = conn_of(handle)
        ended = end_turn(conn, turn_id, outcome=outcome, at=at)
        if ended is None:
            return None
        submission_id, written = ended
        if event is not None:
            write_event(conn, event, at=at)
        if closure is not None:
            written += close_submission(conn, submission_id, outcome=closure, at=at) or ()
        return written

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
            if not wrote:
                return LandingWrite(refused=refused, event_id=None)
            event_id, written = write_landing(conn, landing, revision=revision, at=at)
            return LandingWrite(refused=refused, event_id=event_id, written=written)

    def end_turn_interrupted(
        self, turn_id: str, *, withdrawal: Withdrawal | None, event: TurnEvent, at: datetime
    ) -> tuple[FlowFactWritten, ...] | None:
        with self._store.write("end_turn_interrupted") as conn:
            ended = end_turn(conn, turn_id, outcome=TURN_INTERRUPTED, at=at)
            if ended is None:
                return None
            submission_id, written = ended
            write_event(conn, event, at=at)
            if withdrawal is not None:
                written += close_submission(conn, submission_id, outcome=withdrawal, at=at) or ()
            return written

    def _open(self) -> ColumnElement[bool]:
        """The submission row has no closure."""
        return ~self._store.exists(_closures, _closures.c.submission_id == _sub.c.submission_id)

    def _overviews(self, conn: Connection, rows: Sequence[Row[Any]]) -> list[FlowOverview]:
        """Each flow row with the newest of its record — three batched reads over the flows' ids."""
        flow_ids = [r.flow_id for r in rows]
        newest: dict[str, FlowFact] = {}
        landed: dict[str, FlowFact] = {}
        verdicts: dict[str, FlowFact] = {}
        verdict_from = self._store.scope_join(
            _facts,
            _closures,
            and_(_closures.c.submission_id == _facts.c.submission_id, _closures.c.outcome == _facts.c.outcome),
        )
        for batch in id_batches(flow_ids):
            newest.update(
                (r.flow_id, fact_of(r))
                for r in conn.execute(newest_fact_select(self._store, _facts, _facts.c.flow_id, batch)).all()
            )
            landed.update((f.flow_id, f) for f in self._newest_per_flow(conn, _facts, batch, _facts.c.kind == "landed"))
            verdicts.update(
                (f.flow_id, f)
                for f in self._newest_per_flow(
                    conn,
                    verdict_from,
                    batch,
                    _facts.c.kind == "turn-ended",
                    _facts.c.outcome.not_in(sorted(NOT_VERDICTS)),
                )
            )
        return [
            FlowOverview(
                flow=flow_of(r),
                newest_at=fact.at if (fact := newest.get(r.flow_id)) is not None else None,
                last_verdict=verdicts.get(r.flow_id),
                last_landing=landed.get(r.flow_id),
            )
            for r in rows
        ]

    def _newest_per_flow(
        self, conn: Connection, from_: FromClause, flow_ids: Sequence[str], *criteria: ColumnElement[bool]
    ) -> list[FlowFact]:
        """The newest fact per flow among ``flow_ids`` that ``from_`` and ``criteria`` keep."""
        newest = (
            self._store.select(from_, func.max(_facts.c.id).label("id"))
            .where(_facts.c.flow_id.in_(flow_ids), *criteria)
            .group_by(_facts.c.flow_id)
            .subquery()
        )
        return [fact_of(r) for r in conn.execute(self._store.select(_facts).join(newest, _facts.c.id == newest.c.id))]

    def _take_terms(
        self, conn: Connection, row: Row[Any], flows: Sequence[Flow], candidates: Sequence[LandingTerms]
    ) -> None:
        """Give the submission terms for each repository it holds and has none for: what the visit's newest earlier
        generation took, else its candidate. A repository with neither takes none."""
        held = sorted({flow.repository_id for flow in flows})
        if not held:
            return
        taken = set(
            conn.execute(
                self._store.select(_terms, _terms.c.repository_id).where(_terms.c.submission_id == row.submission_id)
            ).scalars()
        )
        earlier: dict[str, LandingTerms] = {}
        if row.generation > 0:
            joined = self._store.scope_join(_terms, _sub, _terms.c.submission_id == _sub.c.submission_id)
            for prior in conn.execute(
                self._store.select(joined, *_terms.c)
                .where(
                    _sub.c.chunk_id == row.chunk_id,
                    _sub.c.node_id == row.node_id,
                    _sub.c.epoch == row.epoch,
                    _sub.c.generation < row.generation,
                )
                .order_by(_sub.c.generation.desc())
            ):
                earlier.setdefault(prior.repository_id, _terms_of(prior))
        fresh = {candidate.repository_id: candidate for candidate in candidates}
        for repository_id in held:
            if repository_id in taken:
                continue
            chosen = earlier.get(repository_id) or fresh.get(repository_id)
            if chosen is None:
                continue
            conn.execute(
                self._store.insert(_terms).values(
                    submission_id=row.submission_id,
                    repository_id=repository_id,
                    landing_policy=chosen.landing_policy,
                    park_timeout_hours=chosen.park_timeout_hours,
                    landing_position=chosen.landing_position,
                )
            )

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


def _terms_of(row: Row[Any]) -> LandingTerms:
    return LandingTerms(
        repository_id=row.repository_id,
        landing_policy=row.landing_policy,
        park_timeout_hours=row.park_timeout_hours,
        landing_position=row.landing_position,
    )


_flows_conforms: type[IWriteFlows] = FlowStore
_flow_views_conforms: type[IReadFlowViews] = FlowStore
