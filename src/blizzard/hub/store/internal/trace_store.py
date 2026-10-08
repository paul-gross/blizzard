"""SQLAlchemy adapter for the trace export seam (package-private).

Reads closing-fact candidates and whole-chunk :class:`StepFacts`, and appends the cursor's fact
rows (``bzh:facts-not-status``). Hydration is a fixed set of batched statements per id batch,
whatever the chunk count (``bzh:bulk-reconstitution``)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from sqlalchemy import Column, Connection, Table, and_, func, insert, not_, select

from blizzard.foundation.event_log import EventLogKind
from blizzard.foundation.migration_source import MigrationSource
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.chunk.model import UsageFact, WorkRef, WorkRefLabel
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL, Graph, IReadManyGraphs
from blizzard.hub.domain.observability.tracing.cursor import CursorKey
from blizzard.hub.domain.observability.tracing.facts import (
    StepFacts,
    TracedBounce,
    TracedChunkCompletion,
    TracedChunkStop,
    TracedDecision,
    TracedDecisionResolution,
    TracedEpochOwner,
    TracedEscalation,
    TracedHubExecSlot,
    TracedHubPoll,
    TracedLease,
    TracedMigration,
    TracedPause,
    TracedPrerequisiteMet,
    TracedPromotion,
    TracedQuestion,
    TracedRequeue,
    TracedRestart,
    TracedRouteCreation,
    TracedRouteRelease,
    TracedTransition,
)
from blizzard.hub.domain.observability.tracing.repository import (
    ClosingCandidates,
    IReadRunnerNames,
    IReadTraceStatus,
    IWriteTraceCursor,
    TraceCheckpoint,
    TraceExportFailure,
)
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections

_FAILED: EventLogKind = "trace-export-failed"
_LATCH_KINDS: tuple[EventLogKind, ...] = (_FAILED, "trace-export-recovered")

#: Every closing-fact table with its ``(time, id)``-indexed columns
#: (blizzard-product:/delivered/tracing/fleet-spans/spec/spans.md §Closing a step).
_CLOSING_TABLES: tuple[tuple[Table, Column, Column], ...] = (
    (s.transitions, s.transitions.c.recorded_at, s.transitions.c.transition_id),
    (s.decisions, s.decisions.c.submitted_at, s.decisions.c.decision_id),
    (s.chunk_migrations, s.chunk_migrations.c.recorded_at, s.chunk_migrations.c.migration_id),
    (s.escalations, s.escalations.c.recorded_at, s.escalations.c.id),
    (s.chunk_restarts, s.chunk_restarts.c.recorded_at, s.chunk_restarts.c.id),
    (s.route_released, s.route_released.c.released_at, s.route_released.c.id),
    (s.chunk_stopped, s.chunk_stopped.c.stopped_at, s.chunk_stopped.c.id),
    (s.chunk_completed, s.chunk_completed.c.completed_at, s.chunk_completed.c.id),
    (s.epoch_owners, s.epoch_owners.c.recorded_at, s.epoch_owners.c.id),
)


def _by_chunk(conn: Connection, table: Table, batch: Sequence[str], *order: Column) -> dict[str, list]:  # type: ignore[type-arg]
    grouped: dict[str, list] = defaultdict(list)  # type: ignore[type-arg]
    for row in conn.execute(select(table).where(table.c.chunk_id.in_(batch)).order_by(*order)).all():
        grouped[row.chunk_id].append(row)
    return grouped


class TraceStore:
    """The trace export's reads and its cursor's appends."""

    def __init__(
        self, store: HubStoreConnections, *, graphs: IReadManyGraphs, names: IReadRunnerNames, label: WorkRefLabel
    ) -> None:
        self._store = store
        self._graphs = graphs
        self._names = names
        self._label = label

    # --- candidates -----------------------------------------------------------------

    def closing_candidates(self, since: datetime, until: datetime, limit: int) -> ClosingCandidates:
        chunk_ids: dict[str, None] = {}
        frontiers: list[datetime] = []
        newest: list[datetime] = []
        with self._store.read("closing_candidates") as conn:
            for table, at, row_id in _CLOSING_TABLES:
                rows, saturated = self._closing_rows(conn, table, at, row_id, since, until, limit)
                chunk_ids.update((r.chunk_id, None) for r in rows)
                if rows:
                    newest.append(rows[-1].at)
                if saturated:
                    frontiers.append(rows[-1].at)
        return ClosingCandidates(tuple(chunk_ids), min(frontiers, default=None), max(newest, default=None))

    @staticmethod
    def _closing_rows(  # type: ignore[no-untyped-def]
        conn: Connection, table: Table, at: Column, row_id: Column, since: datetime, until: datetime, limit: int
    ):
        """One table's rows in ``(time, id)`` order. A full page that never left ``since`` pages on
        past it, so the frontier it sets is always later than ``since``."""
        columns = (table.c.chunk_id, at.label("at"), row_id.label("row_id"))
        page = conn.execute(select(*columns).where(at >= since, at <= until).order_by(at, row_id).limit(limit)).all()
        rows = list(page)
        while len(page) == limit and rows[-1].at == since:
            last = rows[-1]
            after = not_(and_(at == last.at, row_id <= last.row_id))
            page = conn.execute(
                select(*columns).where(at >= last.at, at <= until, after).order_by(at, row_id).limit(limit)
            ).all()
            rows += page
        return rows, len(page) == limit

    # --- hydration ------------------------------------------------------------------

    def step_facts_for(self, chunk_ids: Sequence[str]) -> dict[str, StepFacts]:
        if not chunk_ids:
            return {}
        built: dict[str, StepFacts] = {}
        with self._store.read("step_facts_for") as conn:
            for batch in id_batches(list(chunk_ids)):
                built.update(self._hydrate(conn, batch))
        graphs = self._graphs.get_many(sorted({g for facts in built.values() for g in _graph_ids(facts)}))
        names = self._names.names_for(sorted({r for facts in built.values() for r in _runner_ids(facts)}))
        return {chunk_id: _with_reads(facts, graphs, names) for chunk_id, facts in built.items()}

    def _hydrate(self, conn: Connection, batch: Sequence[str]) -> dict[str, StepFacts]:
        chunk_rows = {r.chunk_id: r for r in conn.execute(select(s.chunks).where(s.chunks.c.chunk_id.in_(batch)))}
        pins = {chunk_id: row.graph_id for chunk_id, row in chunk_rows.items()}
        if not pins:
            return {}
        ids = list(pins)
        work_refs = _by_chunk(conn, s.chunk_work_refs, ids, s.chunk_work_refs.c.id)
        leases = _by_chunk(conn, s.lease_facts, ids, s.lease_facts.c.id)
        owners = _by_chunk(conn, s.epoch_owners, ids, s.epoch_owners.c.id)
        transitions = _by_chunk(conn, s.transitions, ids, s.transitions.c.recorded_at)
        migrations = _by_chunk(conn, s.chunk_migrations, ids, s.chunk_migrations.c.recorded_at)
        restarts = _by_chunk(conn, s.chunk_restarts, ids, s.chunk_restarts.c.id)
        escalations = _by_chunk(conn, s.escalations, ids, s.escalations.c.id)
        decisions = _by_chunk(conn, s.decisions, ids, s.decisions.c.submitted_at)
        resolutions = self._resolutions(conn, ids)
        requeues = _by_chunk(conn, s.requeues, ids, s.requeues.c.id)
        released = _by_chunk(conn, s.route_released, ids, s.route_released.c.id)
        stopped = _by_chunk(conn, s.chunk_stopped, ids, s.chunk_stopped.c.id)
        completed = _by_chunk(conn, s.chunk_completed, ids, s.chunk_completed.c.id)
        questions = self._questions(conn, ids)
        pauses = _by_chunk(conn, s.chunk_pause_facts, ids, s.chunk_pause_facts.c.id)
        slots = self._slots(conn, ids)
        polls = _by_chunk(conn, s.hub_node_poll, ids, s.hub_node_poll.c.id)
        bounces = _by_chunk(conn, s.chunk_bounces, ids, s.chunk_bounces.c.id)
        created = _by_chunk(conn, s.route_created, ids, s.route_created.c.created_at)
        promoted = _by_chunk(conn, s.chunk_promoted, ids, s.chunk_promoted.c.id)
        usage = _by_chunk(conn, s.usage_facts, ids, s.usage_facts.c.id)
        met = self._prerequisites_met(conn, ids)
        return {
            chunk_id: StepFacts(
                chunk_id=chunk_id,
                pin_graph_id=pin,
                minted_at=chunk_rows[chunk_id].minted_at,
                lease_facts=tuple(TracedLease(r.epoch, r.minted_at) for r in leases[chunk_id]),
                epoch_owners=tuple(TracedEpochOwner(r.epoch, r.runner_id, r.recorded_at) for r in owners[chunk_id]),
                transitions=tuple(_transition(r) for r in transitions[chunk_id]),
                migrations=tuple(_migration(r) for r in migrations[chunk_id]),
                restarts=tuple(_restart(r) for r in restarts[chunk_id]),
                escalations=tuple(
                    TracedEscalation(r.epoch, r.recorded_at, r.decision_id) for r in escalations[chunk_id]
                ),
                decisions=tuple(
                    TracedDecision(r.decision_id, r.node_id, r.epoch, r.submitted_at, r.imposed_by_runner_id)
                    for r in decisions[chunk_id]
                ),
                decision_resolutions=tuple(resolutions[chunk_id]),
                requeues=tuple(TracedRequeue(r.requeued_at) for r in requeues[chunk_id]),
                route_released=tuple(TracedRouteRelease(r.released_at) for r in released[chunk_id]),
                chunk_stopped=tuple(TracedChunkStop(r.stopped_at) for r in stopped[chunk_id]),
                chunk_completed=tuple(TracedChunkCompletion(r.completed_at) for r in completed[chunk_id]),
                questions=tuple(questions[chunk_id]),
                pauses=tuple(TracedPause(str(r.id), r.paused, r.set_at) for r in pauses[chunk_id]),
                hub_exec_slots=tuple(slots[chunk_id]),
                hub_polls=tuple(TracedHubPoll(str(r.id), r.node_id, r.epoch, r.polled_at) for r in polls[chunk_id]),
                bounces=tuple(TracedBounce(r.epoch, r.cause, r.recorded_at) for r in bounces[chunk_id]),
                routes_created=tuple(TracedRouteCreation(r.created_at) for r in created[chunk_id]),
                promotions=tuple(TracedPromotion(r.promoted_at) for r in promoted[chunk_id]),
                prerequisites_met=tuple(met[chunk_id]),
                usage=tuple(_usage(r) for r in usage[chunk_id]),
                work_refs=self._rendered(work_refs[chunk_id]),
                work_sources=self._sources(work_refs[chunk_id]),
            )
            for chunk_id, pin in pins.items()
        }

    def _rendered(self, rows: list) -> tuple[str, ...]:  # type: ignore[type-arg]
        labels = (self._label(WorkRef(source=r.source, ref=r.ref)) for r in rows)
        return tuple(label for label in labels if label is not None)

    def _sources(self, rows: list) -> tuple[str, ...]:  # type: ignore[type-arg]
        """The distinct sources of the rows :meth:`_rendered` keeps, in ref order."""
        kept = (r.source for r in rows if self._label(WorkRef(source=r.source, ref=r.ref)) is not None)
        return tuple(dict.fromkeys(kept))

    @staticmethod
    def _resolutions(conn: Connection, ids: Sequence[str]) -> dict[str, list[TracedDecisionResolution]]:
        stmt = (
            select(s.decisions.c.chunk_id, s.decision_resolutions)
            .join(s.decisions, s.decisions.c.decision_id == s.decision_resolutions.c.decision_id)
            .where(s.decisions.c.chunk_id.in_(ids))
        )
        grouped: dict[str, list[TracedDecisionResolution]] = defaultdict(list)
        for r in conn.execute(stmt).all():
            grouped[r.chunk_id].append(TracedDecisionResolution(r.decision_id, r.resolved_at, r.choice))
        return grouped

    @staticmethod
    def _questions(conn: Connection, ids: Sequence[str]) -> dict[str, list[TracedQuestion]]:
        stmt = (
            select(s.questions, s.question_answers.c.answered_at)
            .outerjoin(s.question_answers, s.question_answers.c.question_id == s.questions.c.question_id)
            .where(s.questions.c.chunk_id.in_(ids))
            .order_by(s.questions.c.asked_at)
        )
        grouped: dict[str, list[TracedQuestion]] = defaultdict(list)
        for r in conn.execute(stmt).all():
            grouped[r.chunk_id].append(TracedQuestion(r.question_id, r.epoch, r.asked_at, r.answered_at))
        return grouped

    @staticmethod
    def _slots(conn: Connection, ids: Sequence[str]) -> dict[str, list[TracedHubExecSlot]]:
        stmt = select(s.hub_exec_slot).where(s.hub_exec_slot.c.holder_chunk_id.in_(ids))
        grouped: dict[str, list[TracedHubExecSlot]] = defaultdict(list)
        for r in conn.execute(stmt.order_by(s.hub_exec_slot.c.acquired_at)).all():
            grouped[r.holder_chunk_id].append(TracedHubExecSlot(r.slot_id, r.node_id, r.acquired_at, r.released_at))
        return grouped

    @staticmethod
    def _prerequisites_met(conn: Connection, ids: Sequence[str]) -> dict[str, list[TracedPrerequisiteMet]]:
        """When each of a chunk's prerequisites first derived ``done``: its first transition into the
        terminal, or its first operator completion, whichever came first."""
        edges = conn.execute(
            select(s.chunk_dependencies.c.dependent_chunk_id, s.chunk_dependencies.c.prerequisite_chunk_id).where(
                s.chunk_dependencies.c.dependent_chunk_id.in_(ids)
            )
        ).all()
        prerequisites = sorted({e.prerequisite_chunk_id for e in edges})
        done_at: dict[str, datetime] = {}
        for batch in id_batches(prerequisites):
            terminal = select(s.transitions.c.chunk_id, func.min(s.transitions.c.recorded_at).label("at")).where(
                s.transitions.c.chunk_id.in_(batch), s.transitions.c.to_node_id == RESERVED_TERMINAL
            )
            completed = select(s.chunk_completed.c.chunk_id, func.min(s.chunk_completed.c.completed_at).label("at"))
            completed = completed.where(s.chunk_completed.c.chunk_id.in_(batch))
            for stmt in (terminal.group_by(s.transitions.c.chunk_id), completed.group_by(s.chunk_completed.c.chunk_id)):
                for r in conn.execute(stmt).all():
                    done_at[r.chunk_id] = min(r.at, done_at.get(r.chunk_id, r.at))
        grouped: dict[str, list[TracedPrerequisiteMet]] = defaultdict(list)
        for edge in edges:
            if edge.prerequisite_chunk_id in done_at:
                grouped[edge.dependent_chunk_id].append(TracedPrerequisiteMet(done_at[edge.prerequisite_chunk_id]))
        return grouped

    # --- cursor and latch -----------------------------------------------------------

    def newest_cursor(self) -> TraceCheckpoint | None:
        return self._newest_cursor("newest_cursor")

    def newest_export_cursor(self) -> TraceCheckpoint | None:
        """The newest row that told spans — rows of zero are jumps and idle advances."""
        return self._newest_cursor("newest_export_cursor", s.trace_cursor.c.span_count > 0)

    def _newest_cursor(self, operation: str, *where) -> TraceCheckpoint | None:  # type: ignore[no-untyped-def]
        c = s.trace_cursor.c
        with self._store.read(operation) as conn:
            row = conn.execute(
                select(s.trace_cursor).where(*where).order_by(c.recorded_at.desc(), c.id.desc()).limit(1)
            ).first()
        if row is None:
            return None
        return TraceCheckpoint(
            CursorKey(row.position_at, row.chunk_id, row.epoch, row.decision_id), row.span_count, row.recorded_at
        )

    def newest_export_failure(self) -> TraceExportFailure | None:
        """The newest failure event — filtered in the query, so a later recovery never hides it."""
        c = s.event_log.c
        stmt = (
            select(c.kind, c.recorded_at, c.message)
            .where(c.kind == _FAILED)
            .order_by(c.recorded_at.desc(), c.id.desc())
            .limit(1)
        )
        with self._store.read("newest_export_failure") as conn:
            row = conn.execute(stmt).first()
        return TraceExportFailure(row.recorded_at, row.message) if row is not None else None

    def newest_export_latch(self) -> EventLogKind | None:
        """Walks the event log newest-first to the first match — run once per process start, not per pass."""
        c = s.event_log.c
        stmt = select(c.kind).where(c.kind.in_(_LATCH_KINDS)).order_by(c.recorded_at.desc(), c.id.desc()).limit(1)
        with self._store.read("newest_export_latch") as conn:
            kind = conn.execute(stmt).scalar_one_or_none()
        return next((k for k in _LATCH_KINDS if k == kind), None)

    def append_cursor(self, record: TraceCheckpoint) -> None:
        position = record.position
        with self._store.write("append_cursor") as conn:
            conn.execute(
                insert(s.trace_cursor).values(
                    position_at=position.at,
                    chunk_id=position.chunk_id,
                    epoch=position.epoch,
                    decision_id=position.decision_id,
                    span_count=record.span_count,
                    recorded_at=record.recorded_at,
                )
            )


def _transition(r) -> TracedTransition:  # type: ignore[no-untyped-def]
    return TracedTransition(
        epoch=r.epoch,
        recorded_at=r.recorded_at,
        graph_id=r.graph_id,
        to_node_id=r.to_node_id,
        from_node_id=r.from_node_id,
        decision_id=r.decision_id,
        choice_name=r.choice_name,
    )


def _migration(r) -> TracedMigration:  # type: ignore[no-untyped-def]
    return TracedMigration(
        epoch=r.epoch,
        recorded_at=r.recorded_at,
        from_graph_id=r.from_graph_id,
        to_graph_id=r.to_graph_id,
        from_node_id=r.from_node_id,
        landed_node_id=r.landed_node_id,
        # Null on a row predating the discriminator — read as unrecorded, never guessed at.
        source=MigrationSource(r.source) if r.source else None,
        decision_id=r.decision_id,
        choice_name=r.choice_name,
    )


def _restart(r) -> TracedRestart:  # type: ignore[no-untyped-def]
    return TracedRestart(
        epoch=r.epoch,
        recorded_at=r.recorded_at,
        graph_id=r.graph_id,
        to_node_id=r.to_node_id,
        from_graph_id=r.from_graph_id,
        from_node_id=r.from_node_id,
        decision_id=r.decision_id,
    )


def _usage(u) -> UsageFact:  # type: ignore[no-untyped-def]
    return UsageFact(
        node_id=u.node_id,
        epoch=u.epoch,
        kind=u.kind,
        model=u.model,
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_read_tokens=u.cache_read_tokens,
        cache_create_tokens=u.cache_create_tokens,
        cost_usd=u.cost_usd,
        recorded_at=u.recorded_at,
        harness_id=u.harness_id,
        harness_version=u.harness_version,
        estimated_cost_usd=u.estimated_cost_usd,
    )


def _graph_ids(facts: StepFacts) -> set[str]:
    """Every graph a chunk's facts name — its pin and each movement fact's graphs."""
    ids = {facts.pin_graph_id} if facts.pin_graph_id is not None else set()
    ids |= {t.graph_id for t in facts.transitions}
    ids |= {m.from_graph_id for m in facts.migrations} | {m.to_graph_id for m in facts.migrations}
    ids |= {r.graph_id for r in facts.restarts}
    ids |= {r.from_graph_id for r in facts.restarts if r.from_graph_id is not None}
    return ids


def _runner_ids(facts: StepFacts) -> set[str]:
    """Every runner a chunk's facts name — each epoch's owner and each gate's imposing runner."""
    ids = {o.runner_id for o in facts.epoch_owners if o.runner_id is not None}
    return ids | {d.imposed_by_runner_id for d in facts.decisions if d.imposed_by_runner_id is not None}


def _with_reads(facts: StepFacts, graphs: dict[str, Graph], names: dict[str, str]) -> StepFacts:
    return replace(
        facts,
        graphs={g: graphs[g] for g in _graph_ids(facts) if g in graphs},
        runner_names={r: names[r] for r in _runner_ids(facts) if r in names},
    )


def _conforms_trace_store(x: TraceStore) -> tuple[IWriteTraceCursor, IReadTraceStatus]:
    return x, x
