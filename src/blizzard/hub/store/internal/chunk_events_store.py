"""SQLAlchemy adapter for the chunk events seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Column, ColumnElement, Connection, Row, Select, Table, and_, or_, true

from blizzard.foundation.clock import IClock
from blizzard.foundation.event_log import (
    EVENT_LOG_SEVERITY,
    EventLogSeverity,
    narrow_event_log_kind,
    narrow_event_log_severity,
)
from blizzard.foundation.hub_event_types import ActivityChunkChangeCause
from blizzard.foundation.roles import adapter_model
from blizzard.hub.domain.chunk.model import (
    DEFAULT_EVENT_LIST_LIMIT,
    ActivityEntry,
    OperationalEvent,
    RouteReleaseCause,
)
from blizzard.hub.domain.chunk.ports.events import FeedLens, IWriteChunkEventsRepository
from blizzard.hub.domain.kernel.transactions import Transaction
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.fact_families import FactTable, entry_of
from blizzard.hub.store.internal.project_reach import chunks_of_project
from blizzard.hub.store.scoped import ScopedConnections, ScopedStatements
from blizzard.hub.store.transactions import connection_in

# The hub coordinator's own reserved ``transitions.runner_id`` — the only fact-table
# difference between a ``hub-advanced`` and a ``node-completed`` transition.
_HUB_RUNNER_ID = "hub"

# --- activity_facts_since's per-source base statements: each is what `_bounded` (below)
# adds its `WHERE ts >= :since ORDER BY ts DESC, pk DESC LIMIT :limit` to. Named and
# module-level so `tests/component/hub/store/internal/test_chunk_fact_table_indexes.py` compiles the real statement —
# real joins and anti-join included.


def bounded_stmt(
    stmt: Select[Any], *, ts_col: Column[Any], pk_col: Column[Any], since: datetime, limit: int
) -> Select[Any]:
    """``activity_facts_since``'s shared bound over a source's base statement (never a full-table scan) — a pure
    builder that lets a test compile the exact statement a source executes."""
    return stmt.where(ts_col >= since).order_by(ts_col.desc(), pk_col.desc()).limit(limit)


def _event_log_in_lens(stmts: ScopedStatements, lens: FeedLens) -> ColumnElement[bool]:
    """An ``event_log`` row :class:`FeedLens` keeps: one naming a chunk of its project, or one naming no chunk whose
    runner, if it names one, serves the project."""
    t = s.event_log
    return or_(
        t.c.chunk_id.in_(chunks_of_project(stmts, lens.project)),
        and_(t.c.chunk_id.is_(None), or_(t.c.runner_id.is_(None), t.c.runner_id.in_(sorted(lens.runners)))),
    )


def _narrow_persisted_severity(*, kind: str, severity: str) -> EventLogSeverity:
    """A row's recognized ``severity``, or its recognized ``kind``'s severity
    when the stored severity is unknown. An unknown kind defaults to ``critical``."""
    narrowed = narrow_event_log_severity(severity)
    if narrowed is not None:
        return narrowed
    narrowed_kind = narrow_event_log_kind(kind)
    return EVENT_LOG_SEVERITY[narrowed_kind] if narrowed_kind is not None else EventLogSeverity.CRITICAL


def deleted_chunk_ids_stmt(stmts: ScopedStatements) -> Select[Any]:
    return stmts.select(s.chunk_deleted, s.chunk_deleted.c.chunk_id)


def chunk_minted_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(s.chunks, s.chunks.c.chunk_id, s.chunks.c.graph_id, s.chunks.c.minted_at).where(
        s.chunks.c.chunk_id.not_in(deleted)
    )


def chunk_promoted_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.chunk_promoted, s.chunks, s.chunks.c.chunk_id == s.chunk_promoted.c.chunk_id),
        s.chunk_promoted.c.id,
        s.chunk_promoted.c.chunk_id,
        s.chunk_promoted.c.promoted_at,
        s.chunks.c.graph_id,
    ).where(s.chunk_promoted.c.chunk_id.not_in(deleted))


def _chunk_demoted_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.chunk_demoted, s.chunks, s.chunks.c.chunk_id == s.chunk_demoted.c.chunk_id),
        s.chunk_demoted.c.id,
        s.chunk_demoted.c.chunk_id,
        s.chunk_demoted.c.demoted_at,
        s.chunks.c.graph_id,
    ).where(s.chunk_demoted.c.chunk_id.not_in(deleted))


def chunk_grouped_stmt(stmts: ScopedStatements) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.chunk_grouped, s.chunks, s.chunks.c.chunk_id == s.chunk_grouped.c.chunk_id),
        s.chunk_grouped.c.id,
        s.chunk_grouped.c.chunk_id,
        s.chunk_grouped.c.grouped_at,
        s.chunks.c.graph_id,
    )


def route_created_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.route_created, s.chunks, s.chunks.c.chunk_id == s.route_created.c.chunk_id),
        s.route_created.c.route_id,
        s.route_created.c.chunk_id,
        s.route_created.c.runner_id,
        s.route_created.c.created_at,
        s.chunks.c.graph_id,
    ).where(s.route_created.c.chunk_id.not_in(deleted))


def transitions_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        s.transitions,
        s.transitions.c.transition_id,
        s.transitions.c.chunk_id,
        s.transitions.c.runner_id,
        s.transitions.c.graph_id,
        s.transitions.c.recorded_at,
    ).where(s.transitions.c.chunk_id.not_in(deleted))


def chunk_migrations_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        s.chunk_migrations,
        s.chunk_migrations.c.migration_id,
        s.chunk_migrations.c.chunk_id,
        s.chunk_migrations.c.to_graph_id,
        s.chunk_migrations.c.recorded_at,
    ).where(s.chunk_migrations.c.chunk_id.not_in(deleted))


def chunk_restarts_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.chunk_restarts, s.chunks, s.chunks.c.chunk_id == s.chunk_restarts.c.chunk_id),
        s.chunk_restarts.c.id,
        s.chunk_restarts.c.chunk_id,
        s.chunk_restarts.c.recorded_at,
        s.chunks.c.graph_id,
    ).where(s.chunk_restarts.c.chunk_id.not_in(deleted))


def decisions_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.decisions, s.chunks, s.chunks.c.chunk_id == s.decisions.c.chunk_id),
        s.decisions.c.decision_id,
        s.decisions.c.chunk_id,
        s.decisions.c.submitted_at,
        s.chunks.c.graph_id,
    ).where(s.decisions.c.chunk_id.not_in(deleted))


def decision_resolutions_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    resolved = stmts.scope_join(
        s.decision_resolutions, s.decisions, s.decisions.c.decision_id == s.decision_resolutions.c.decision_id
    )
    return stmts.select(
        stmts.scope_join(resolved, s.chunks, s.chunks.c.chunk_id == s.decisions.c.chunk_id),
        s.decision_resolutions.c.decision_id,
        s.decisions.c.chunk_id,
        s.decision_resolutions.c.resolved_at,
        s.chunks.c.graph_id,
    ).where(s.decisions.c.chunk_id.not_in(deleted))


def questions_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.questions, s.chunks, s.chunks.c.chunk_id == s.questions.c.chunk_id),
        s.questions.c.question_id,
        s.questions.c.chunk_id,
        s.questions.c.runner_id,
        s.questions.c.asked_at,
        s.chunks.c.graph_id,
    ).where(s.questions.c.chunk_id.not_in(deleted))


def question_answers_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    answered = stmts.scope_join(
        s.question_answers,
        s.questions,
        (s.questions.c.tenant_id == s.question_answers.c.tenant_id)
        & (s.questions.c.question_id == s.question_answers.c.question_id),
    )
    return stmts.select(
        stmts.scope_join(answered, s.chunks, s.chunks.c.chunk_id == s.questions.c.chunk_id),
        s.question_answers.c.question_id,
        s.questions.c.chunk_id,
        s.question_answers.c.answered_at,
        s.chunks.c.graph_id,
    ).where(s.questions.c.chunk_id.not_in(deleted))


def escalations_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.escalations, s.chunks, s.chunks.c.chunk_id == s.escalations.c.chunk_id),
        s.escalations.c.id,
        s.escalations.c.chunk_id,
        s.escalations.c.recorded_at,
        s.chunks.c.graph_id,
    ).where(s.escalations.c.chunk_id.not_in(deleted))


def requeues_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.requeues, s.chunks, s.chunks.c.chunk_id == s.requeues.c.chunk_id),
        s.requeues.c.id,
        s.requeues.c.chunk_id,
        s.requeues.c.requeued_at,
        s.chunks.c.graph_id,
    ).where(s.requeues.c.chunk_id.not_in(deleted))


def route_released_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    """Each release with the runner whose route it released: the chunk's ``route_created`` with the
    greatest ``seq`` below the release's, resolved per row inside the bounded page statement."""
    released, created = s.route_released, s.route_created
    holder = (
        stmts.select(created, created.c.runner_id)
        .where(created.c.chunk_id == released.c.chunk_id, created.c.seq < released.c.seq)
        # (created_at, seq) is the route history's own order, and the chunk-keyed index's: no sort.
        .order_by(created.c.created_at.desc(), created.c.seq.desc())
        .limit(1)
        .correlate(released)
        .scalar_subquery()
    )
    return stmts.select(
        stmts.scope_join(released, s.chunks, s.chunks.c.chunk_id == released.c.chunk_id),
        released.c.id,
        released.c.chunk_id,
        released.c.released_at,
        released.c.cause,
        released.c.reason,
        holder.label("holder_runner_id"),
        s.chunks.c.graph_id,
    ).where(released.c.chunk_id.not_in(deleted))


def _route_released_entry(r: Row[Any]) -> ActivityEntry:
    """A ``route_released`` row as its feed entry: a release reads ``released``, naming its holder and its
    reason; every other cause, a null one included, reads ``detached``."""
    if r.cause == RouteReleaseCause.RELEASE:
        return ActivityEntry(
            type="chunk-changed",
            key=f"route_released:{r.id}",
            at=r.released_at,
            chunk_id=r.chunk_id,
            runner_id=r.holder_runner_id,
            cause="released",
            graph_id=r.graph_id,
            reason=r.reason,
        )
    return ActivityEntry(
        type="chunk-changed",
        key=f"route_released:{r.id}",
        at=r.released_at,
        chunk_id=r.chunk_id,
        cause="detached",
        graph_id=r.graph_id,
    )


def chunk_pause_facts_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.chunk_pause_facts, s.chunks, s.chunks.c.chunk_id == s.chunk_pause_facts.c.chunk_id),
        s.chunk_pause_facts.c.id,
        s.chunk_pause_facts.c.chunk_id,
        s.chunk_pause_facts.c.paused,
        s.chunk_pause_facts.c.set_at,
        s.chunks.c.graph_id,
    ).where(s.chunk_pause_facts.c.chunk_id.not_in(deleted))


def chunk_stopped_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.chunk_stopped, s.chunks, s.chunks.c.chunk_id == s.chunk_stopped.c.chunk_id),
        s.chunk_stopped.c.id,
        s.chunk_stopped.c.chunk_id,
        s.chunk_stopped.c.stopped_at,
        s.chunks.c.graph_id,
    ).where(s.chunk_stopped.c.chunk_id.not_in(deleted))


def chunk_completed_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.chunk_completed, s.chunks, s.chunks.c.chunk_id == s.chunk_completed.c.chunk_id),
        s.chunk_completed.c.id,
        s.chunk_completed.c.chunk_id,
        s.chunk_completed.c.completed_at,
        s.chunks.c.graph_id,
    ).where(s.chunk_completed.c.chunk_id.not_in(deleted))


def chunk_deleted_activity_stmt(stmts: ScopedStatements) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.chunk_deleted, s.chunks, s.chunks.c.chunk_id == s.chunk_deleted.c.chunk_id),
        s.chunk_deleted.c.id,
        s.chunk_deleted.c.chunk_id,
        s.chunk_deleted.c.deleted_at,
        s.chunk_deleted.c.deleted_by,
        s.chunks.c.graph_id,
    )


@adapter_model
@dataclass(frozen=True)
class _EventSource:
    """How one fact table reads as feed entries: its base statement and what each row's entry says. The key, instant
    and paging columns come from the table's registry entry, never from here."""

    statement: Callable[[ScopedStatements, Select[Any]], Select[Any]]
    cause: ActivityChunkChangeCause | Callable[[Row[Any]], ActivityChunkChangeCause]
    graph_column: str = "graph_id"
    runner: bool = False
    by_column: str | None = None
    entry: Callable[[Row[Any]], ActivityEntry] | None = None


def _event_builder(source: _EventSource, entry: FactTable) -> Callable[[Row[Any]], ActivityEntry]:
    """The row-to-entry mapper of ``source``, keyed and timed by the registry ``entry``."""
    if source.entry is not None:
        return source.entry
    assert entry.fact_time is not None
    pk, at = entry.row_id.key, entry.fact_time.key

    def build(r: Row[Any]) -> ActivityEntry:
        return ActivityEntry(
            type="chunk-changed",
            key=f"{entry.table.name}:{getattr(r, pk)}",
            at=getattr(r, at),
            chunk_id=r.chunk_id,
            runner_id=r.runner_id if source.runner else None,
            cause=source.cause(r) if callable(source.cause) else source.cause,
            graph_id=getattr(r, source.graph_column),
            by=getattr(r, source.by_column) if source.by_column else None,
        )

    return build


def _transition_cause(r: Row[Any]) -> ActivityChunkChangeCause:
    return "hub-advanced" if r.runner_id == _HUB_RUNNER_ID else "node-completed"


def _pause_cause(r: Row[Any]) -> ActivityChunkChangeCause:
    return "paused" if r.paused else "resumed"


#: The activity feed's fact-table sources, in feed order. The keys equal the registry's events narrowing
#: (``tests/unit/hub/store/internal/test_fact_families.py``); ``chunks`` is the feed's root and is read apart.
EVENT_SOURCES: dict[Table, _EventSource] = {
    s.chunk_promoted: _EventSource(chunk_promoted_stmt, "promoted"),
    s.chunk_demoted: _EventSource(_chunk_demoted_stmt, "demoted"),
    s.chunk_grouped: _EventSource(lambda stmts, _deleted: chunk_grouped_stmt(stmts), "grouped"),
    s.route_created: _EventSource(route_created_activity_stmt, "claimed", runner=True),
    s.transitions: _EventSource(transitions_activity_stmt, _transition_cause, runner=True),
    s.chunk_migrations: _EventSource(chunk_migrations_activity_stmt, "migrated", graph_column="to_graph_id"),
    s.chunk_restarts: _EventSource(chunk_restarts_activity_stmt, "restarted"),
    s.decisions: _EventSource(decisions_activity_stmt, "decision-submitted"),
    s.decision_resolutions: _EventSource(decision_resolutions_activity_stmt, "decision-resolved"),
    s.questions: _EventSource(questions_activity_stmt, "question-asked", runner=True),
    s.question_answers: _EventSource(question_answers_activity_stmt, "question-answered"),
    s.escalations: _EventSource(escalations_activity_stmt, "escalated"),
    s.requeues: _EventSource(requeues_activity_stmt, "requeued"),
    s.route_released: _EventSource(route_released_activity_stmt, "released", entry=_route_released_entry),
    s.chunk_pause_facts: _EventSource(chunk_pause_facts_activity_stmt, _pause_cause),
    s.chunk_stopped: _EventSource(chunk_stopped_activity_stmt, "stopped"),
    s.chunk_completed: _EventSource(chunk_completed_activity_stmt, "completed"),
    s.chunk_deleted: _EventSource(
        lambda stmts, _deleted: chunk_deleted_activity_stmt(stmts), "deleted", by_column="deleted_by"
    ),
}


class ChunkEventsStore:
    """The fleet's operational event log and the per-chunk change-activity feed."""

    def __init__(self, store: ScopedConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def list_events(
        self,
        *,
        severity: EventLogSeverity | None = None,
        runner_id: str | None = None,
        chunk_id: str | None = None,
        since: datetime | None = None,
        limit: int = DEFAULT_EVENT_LIST_LIMIT,
        lens: FeedLens | None = None,
    ) -> list[OperationalEvent]:
        with self._store.read("list_events") as conn:
            stmt = self._store.select(s.event_log)
            if severity is not None:
                stmt = stmt.where(s.event_log.c.severity == severity)
            if runner_id is not None:
                stmt = stmt.where(s.event_log.c.runner_id == runner_id)
            if chunk_id is not None:
                stmt = stmt.where(s.event_log.c.chunk_id == chunk_id)
            if since is not None:
                stmt = stmt.where(s.event_log.c.recorded_at >= since)
            if lens is not None:
                stmt = stmt.where(_event_log_in_lens(self._store, lens))
            stmt = stmt.order_by(s.event_log.c.recorded_at.desc(), s.event_log.c.id.desc()).limit(limit)
            return [
                OperationalEvent(
                    id=row.id,
                    recorded_at=row.recorded_at,
                    severity=_narrow_persisted_severity(kind=row.kind, severity=row.severity),
                    kind=row.kind,
                    runner_id=row.runner_id,
                    chunk_id=row.chunk_id,
                    lease_id=row.lease_id,
                    node_name=row.node_name,
                    message=row.message,
                    detail=json.loads(row.detail) if row.detail is not None else None,
                )
                for row in conn.execute(stmt).all()
            ]

    def activity_events_since(
        self, since: datetime, *, limit: int, lens: FeedLens | None = None
    ) -> list[OperationalEvent]:
        """`src/blizzard/hub/domain/chunk/ports/events.py`'s `IReadChunkEventsRepository.activity_events_since`."""
        with self._store.read("activity_events_since") as conn:
            deleted = deleted_chunk_ids_stmt(self._store)
            stmt = (
                self._store.select(s.event_log)
                .where(s.event_log.c.recorded_at >= since)
                .where(or_(s.event_log.c.chunk_id.is_(None), s.event_log.c.chunk_id.not_in(deleted)))
                .where(_event_log_in_lens(self._store, lens) if lens is not None else true())
                .order_by(s.event_log.c.recorded_at.desc(), s.event_log.c.id.desc())
                .limit(limit)
            )
            return [
                OperationalEvent(
                    id=row.id,
                    recorded_at=row.recorded_at,
                    severity=_narrow_persisted_severity(kind=row.kind, severity=row.severity),
                    kind=row.kind,
                    runner_id=row.runner_id,
                    chunk_id=row.chunk_id,
                    lease_id=row.lease_id,
                    node_name=row.node_name,
                    message=row.message,
                    detail=json.loads(row.detail) if row.detail is not None else None,
                )
                for row in conn.execute(stmt).all()
            ]

    def activity_facts_since(
        self, since: datetime, *, limit: int, project: ProjectId | None = None
    ) -> list[ActivityEntry]:
        """See
        :meth:`~blizzard.hub.domain.chunk.ports.events.IReadChunkEventsRepository.activity_facts_since` — one
        bounded read per mapped ``ChunkChangeCause`` fact table, concatenated, unsorted across
        sources. Every per-chunk source joins ``chunks`` for its current ``graph_id``, except
        ``transitions``/``chunk_migrations``, which carry their own column."""
        with self._store.read("activity_facts_since") as conn:
            rows: list[ActivityEntry] = []
            # Resolved once: every fact-source block below excludes a
            # deleted chunk by referencing this same subquery, rather than repeating it.
            deleted = deleted_chunk_ids_stmt(self._store)
            chunks = chunks_of_project(self._store, project) if project is not None else None
            rows += self._bounded(
                conn,
                chunk_minted_stmt(self._store, deleted),
                ts_col=s.chunks.c.minted_at,
                pk_col=s.chunks.c.chunk_id,
                since=since,
                limit=limit,
                chunks=chunks,
                builder=lambda r: ActivityEntry(
                    type="chunk-changed",
                    key=f"chunks:{r.chunk_id}",
                    at=r.minted_at,
                    chunk_id=r.chunk_id,
                    cause="minted",
                    graph_id=r.graph_id,
                ),
            )
            for table, source in EVENT_SOURCES.items():
                entry = entry_of(table)
                assert entry.fact_time is not None
                rows += self._bounded(
                    conn,
                    source.statement(self._store, deleted),
                    ts_col=entry.fact_time,
                    pk_col=entry.row_id,
                    since=since,
                    limit=limit,
                    chunks=chunks,
                    builder=_event_builder(source, entry),
                )
            return rows

    @staticmethod
    def _bounded(
        conn: Connection,
        stmt: Select[Any],
        *,
        ts_col: Column[Any],
        pk_col: Column[Any],
        since: datetime,
        limit: int,
        chunks: Select[Any] | None,
        builder: Callable[[Row[Any]], ActivityEntry],
    ) -> list[ActivityEntry]:
        """Run one source's own ``ORDER BY <ts> DESC, <pk> DESC LIMIT :limit`` bounded
        read and reshape each row via ``builder`` — the one piece every
        :meth:`activity_facts_since` source shares (never a full-table scan). ``chunks``, when given, narrows
        the source to the facts of those chunks: every source selects the chunk it names as ``chunk_id``."""
        if chunks is not None:
            stmt = stmt.where(stmt.selected_columns.chunk_id.in_(chunks))
        bounded = bounded_stmt(stmt, ts_col=ts_col, pk_col=pk_col, since=since, limit=limit)
        return [builder(row) for row in conn.execute(bounded).all()]

    def record_event(
        self,
        *,
        severity: EventLogSeverity,
        kind: str,
        runner_id: str | None,
        chunk_id: str | None,
        lease_id: str | None,
        node_name: str | None,
        message: str,
        detail: Mapping[str, object] | None,
        at: datetime,
        transaction: Transaction | None = None,
    ) -> int:
        # Append-only operational fact, no epoch fence. `detail` serializes
        # to JSON text here; `chunk_id` is None for a runner-scoped event.
        with connection_in(self._store, transaction, "record_event", write=True) as conn:
            result = conn.execute(
                self._store.insert(s.event_log).values(
                    severity=severity,
                    kind=kind,
                    runner_id=runner_id,
                    chunk_id=chunk_id,
                    lease_id=lease_id,
                    node_name=node_name,
                    message=message,
                    detail=json.dumps(detail) if detail is not None else None,
                    recorded_at=at,
                )
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0


_events_conforms: type[IWriteChunkEventsRepository] = ChunkEventsStore
