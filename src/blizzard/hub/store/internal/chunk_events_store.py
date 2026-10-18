"""SQLAlchemy adapter for the chunk events seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Select, and_, or_, true

from blizzard.foundation.clock import IClock
from blizzard.foundation.event_log import (
    EVENT_LOG_SEVERITY,
    EventLogSeverity,
    narrow_event_log_kind,
    narrow_event_log_severity,
)
from blizzard.hub.domain.chunk.model import DEFAULT_EVENT_LIST_LIMIT, ActivityEntry, OperationalEvent
from blizzard.hub.domain.chunk.ports.events import FeedLens, IWriteChunkEventsRepository
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.project_reach import chunks_of_project
from blizzard.hub.store.scoped import ScopedConnections, ScopedStatements

# The hub coordinator's own reserved ``transitions.runner_id`` — the only fact-table
# difference between a ``hub-advanced`` and a ``node-completed`` transition.
_HUB_RUNNER_ID = "hub"

# --- activity_facts_since's per-source base statements: each is what `_bounded` (below)
# adds its `WHERE ts >= :since ORDER BY ts DESC, pk DESC LIMIT :limit` to. Named and
# module-level so `tests/test_chunk_fact_table_indexes.py` compiles the real statement —
# real joins and anti-join included.


def _bounded_stmt(stmt: Select[Any], *, ts_col: Any, pk_col: Any, since: datetime, limit: int) -> Select[Any]:
    """``activity_facts_since``'s shared bound over a source's base statement (never a full-table scan) — a pure
    builder that lets a test compile the exact statement a source executes."""
    return stmt.where(ts_col >= since).order_by(ts_col.desc(), pk_col.desc()).limit(limit)


def _event_log_in_lens(stmts: ScopedStatements, lens: FeedLens) -> Any:
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


def _deleted_chunk_ids_stmt(stmts: ScopedStatements) -> Select[Any]:
    return stmts.select(s.chunk_deleted, s.chunk_deleted.c.chunk_id)


def _chunk_minted_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(s.chunks, s.chunks.c.chunk_id, s.chunks.c.graph_id, s.chunks.c.minted_at).where(
        s.chunks.c.chunk_id.not_in(deleted)
    )


def _chunk_promoted_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
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


def _chunk_grouped_stmt(stmts: ScopedStatements) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.chunk_grouped, s.chunks, s.chunks.c.chunk_id == s.chunk_grouped.c.chunk_id),
        s.chunk_grouped.c.id,
        s.chunk_grouped.c.chunk_id,
        s.chunk_grouped.c.grouped_at,
        s.chunks.c.graph_id,
    )


def _route_created_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.route_created, s.chunks, s.chunks.c.chunk_id == s.route_created.c.chunk_id),
        s.route_created.c.route_id,
        s.route_created.c.chunk_id,
        s.route_created.c.runner_id,
        s.route_created.c.created_at,
        s.chunks.c.graph_id,
    ).where(s.route_created.c.chunk_id.not_in(deleted))


def _transitions_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        s.transitions,
        s.transitions.c.transition_id,
        s.transitions.c.chunk_id,
        s.transitions.c.runner_id,
        s.transitions.c.graph_id,
        s.transitions.c.recorded_at,
    ).where(s.transitions.c.chunk_id.not_in(deleted))


def _chunk_migrations_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        s.chunk_migrations,
        s.chunk_migrations.c.migration_id,
        s.chunk_migrations.c.chunk_id,
        s.chunk_migrations.c.to_graph_id,
        s.chunk_migrations.c.recorded_at,
    ).where(s.chunk_migrations.c.chunk_id.not_in(deleted))


def _chunk_restarts_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.chunk_restarts, s.chunks, s.chunks.c.chunk_id == s.chunk_restarts.c.chunk_id),
        s.chunk_restarts.c.id,
        s.chunk_restarts.c.chunk_id,
        s.chunk_restarts.c.recorded_at,
        s.chunks.c.graph_id,
    ).where(s.chunk_restarts.c.chunk_id.not_in(deleted))


def _decisions_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.decisions, s.chunks, s.chunks.c.chunk_id == s.decisions.c.chunk_id),
        s.decisions.c.decision_id,
        s.decisions.c.chunk_id,
        s.decisions.c.submitted_at,
        s.chunks.c.graph_id,
    ).where(s.decisions.c.chunk_id.not_in(deleted))


def _decision_resolutions_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
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


def _questions_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.questions, s.chunks, s.chunks.c.chunk_id == s.questions.c.chunk_id),
        s.questions.c.question_id,
        s.questions.c.chunk_id,
        s.questions.c.runner_id,
        s.questions.c.asked_at,
        s.chunks.c.graph_id,
    ).where(s.questions.c.chunk_id.not_in(deleted))


def _question_answers_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
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


def _escalations_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.escalations, s.chunks, s.chunks.c.chunk_id == s.escalations.c.chunk_id),
        s.escalations.c.id,
        s.escalations.c.chunk_id,
        s.escalations.c.recorded_at,
        s.chunks.c.graph_id,
    ).where(s.escalations.c.chunk_id.not_in(deleted))


def _requeues_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.requeues, s.chunks, s.chunks.c.chunk_id == s.requeues.c.chunk_id),
        s.requeues.c.id,
        s.requeues.c.chunk_id,
        s.requeues.c.requeued_at,
        s.chunks.c.graph_id,
    ).where(s.requeues.c.chunk_id.not_in(deleted))


def _route_released_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.route_released, s.chunks, s.chunks.c.chunk_id == s.route_released.c.chunk_id),
        s.route_released.c.id,
        s.route_released.c.chunk_id,
        s.route_released.c.released_at,
        s.chunks.c.graph_id,
    ).where(s.route_released.c.chunk_id.not_in(deleted))


def _chunk_pause_facts_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.chunk_pause_facts, s.chunks, s.chunks.c.chunk_id == s.chunk_pause_facts.c.chunk_id),
        s.chunk_pause_facts.c.id,
        s.chunk_pause_facts.c.chunk_id,
        s.chunk_pause_facts.c.paused,
        s.chunk_pause_facts.c.set_at,
        s.chunks.c.graph_id,
    ).where(s.chunk_pause_facts.c.chunk_id.not_in(deleted))


def _chunk_stopped_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.chunk_stopped, s.chunks, s.chunks.c.chunk_id == s.chunk_stopped.c.chunk_id),
        s.chunk_stopped.c.id,
        s.chunk_stopped.c.chunk_id,
        s.chunk_stopped.c.stopped_at,
        s.chunks.c.graph_id,
    ).where(s.chunk_stopped.c.chunk_id.not_in(deleted))


def _chunk_completed_activity_stmt(stmts: ScopedStatements, deleted: Select[Any]) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.chunk_completed, s.chunks, s.chunks.c.chunk_id == s.chunk_completed.c.chunk_id),
        s.chunk_completed.c.id,
        s.chunk_completed.c.chunk_id,
        s.chunk_completed.c.completed_at,
        s.chunks.c.graph_id,
    ).where(s.chunk_completed.c.chunk_id.not_in(deleted))


def _chunk_deleted_activity_stmt(stmts: ScopedStatements) -> Select[Any]:
    return stmts.select(
        stmts.scope_join(s.chunk_deleted, s.chunks, s.chunks.c.chunk_id == s.chunk_deleted.c.chunk_id),
        s.chunk_deleted.c.id,
        s.chunk_deleted.c.chunk_id,
        s.chunk_deleted.c.deleted_at,
        s.chunk_deleted.c.deleted_by,
        s.chunks.c.graph_id,
    )


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
        """See
        :meth:`~blizzard.hub.domain.chunk.ports.events.IReadChunkEventsRepository.activity_events_since`."""
        with self._store.read("activity_events_since") as conn:
            deleted = _deleted_chunk_ids_stmt(self._store)
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
            deleted = _deleted_chunk_ids_stmt(self._store)
            chunks = chunks_of_project(self._store, project) if project is not None else None
            rows += self._bounded(
                conn,
                _chunk_minted_stmt(self._store, deleted),
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
            rows += self._bounded(
                conn,
                _chunk_promoted_stmt(self._store, deleted),
                ts_col=s.chunk_promoted.c.promoted_at,
                pk_col=s.chunk_promoted.c.id,
                since=since,
                limit=limit,
                chunks=chunks,
                builder=lambda r: ActivityEntry(
                    type="chunk-changed",
                    key=f"chunk_promoted:{r.id}",
                    at=r.promoted_at,
                    chunk_id=r.chunk_id,
                    cause="promoted",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                _chunk_demoted_stmt(self._store, deleted),
                ts_col=s.chunk_demoted.c.demoted_at,
                pk_col=s.chunk_demoted.c.id,
                since=since,
                limit=limit,
                chunks=chunks,
                builder=lambda r: ActivityEntry(
                    type="chunk-changed",
                    key=f"chunk_demoted:{r.id}",
                    at=r.demoted_at,
                    chunk_id=r.chunk_id,
                    cause="demoted",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                _chunk_grouped_stmt(self._store),
                ts_col=s.chunk_grouped.c.grouped_at,
                pk_col=s.chunk_grouped.c.id,
                since=since,
                limit=limit,
                chunks=chunks,
                builder=lambda r: ActivityEntry(
                    type="chunk-changed",
                    key=f"chunk_grouped:{r.id}",
                    at=r.grouped_at,
                    chunk_id=r.chunk_id,
                    cause="grouped",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                _route_created_activity_stmt(self._store, deleted),
                ts_col=s.route_created.c.created_at,
                pk_col=s.route_created.c.route_id,
                since=since,
                limit=limit,
                chunks=chunks,
                builder=lambda r: ActivityEntry(
                    type="chunk-changed",
                    key=f"route_created:{r.route_id}",
                    at=r.created_at,
                    chunk_id=r.chunk_id,
                    runner_id=r.runner_id,
                    cause="claimed",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                _transitions_activity_stmt(self._store, deleted),
                ts_col=s.transitions.c.recorded_at,
                pk_col=s.transitions.c.transition_id,
                since=since,
                limit=limit,
                chunks=chunks,
                builder=lambda r: ActivityEntry(
                    type="chunk-changed",
                    key=f"transitions:{r.transition_id}",
                    at=r.recorded_at,
                    chunk_id=r.chunk_id,
                    runner_id=r.runner_id,
                    cause="hub-advanced" if r.runner_id == _HUB_RUNNER_ID else "node-completed",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                _chunk_migrations_activity_stmt(self._store, deleted),
                ts_col=s.chunk_migrations.c.recorded_at,
                pk_col=s.chunk_migrations.c.migration_id,
                since=since,
                limit=limit,
                chunks=chunks,
                builder=lambda r: ActivityEntry(
                    type="chunk-changed",
                    key=f"chunk_migrations:{r.migration_id}",
                    at=r.recorded_at,
                    chunk_id=r.chunk_id,
                    cause="migrated",
                    graph_id=r.to_graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                _chunk_restarts_activity_stmt(self._store, deleted),
                ts_col=s.chunk_restarts.c.recorded_at,
                pk_col=s.chunk_restarts.c.id,
                since=since,
                limit=limit,
                chunks=chunks,
                builder=lambda r: ActivityEntry(
                    type="chunk-changed",
                    key=f"chunk_restarts:{r.id}",
                    at=r.recorded_at,
                    chunk_id=r.chunk_id,
                    cause="restarted",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                _decisions_activity_stmt(self._store, deleted),
                ts_col=s.decisions.c.submitted_at,
                pk_col=s.decisions.c.decision_id,
                since=since,
                limit=limit,
                chunks=chunks,
                builder=lambda r: ActivityEntry(
                    type="chunk-changed",
                    key=f"decisions:{r.decision_id}",
                    at=r.submitted_at,
                    chunk_id=r.chunk_id,
                    cause="decision-submitted",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                _decision_resolutions_activity_stmt(self._store, deleted),
                ts_col=s.decision_resolutions.c.resolved_at,
                pk_col=s.decision_resolutions.c.decision_id,
                since=since,
                limit=limit,
                chunks=chunks,
                builder=lambda r: ActivityEntry(
                    type="chunk-changed",
                    key=f"decision_resolutions:{r.decision_id}",
                    at=r.resolved_at,
                    chunk_id=r.chunk_id,
                    cause="decision-resolved",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                _questions_activity_stmt(self._store, deleted),
                ts_col=s.questions.c.asked_at,
                pk_col=s.questions.c.question_id,
                since=since,
                limit=limit,
                chunks=chunks,
                builder=lambda r: ActivityEntry(
                    type="chunk-changed",
                    key=f"questions:{r.question_id}",
                    at=r.asked_at,
                    chunk_id=r.chunk_id,
                    runner_id=r.runner_id,
                    cause="question-asked",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                _question_answers_activity_stmt(self._store, deleted),
                ts_col=s.question_answers.c.answered_at,
                pk_col=s.question_answers.c.question_id,
                since=since,
                limit=limit,
                chunks=chunks,
                builder=lambda r: ActivityEntry(
                    type="chunk-changed",
                    key=f"question_answers:{r.question_id}",
                    at=r.answered_at,
                    chunk_id=r.chunk_id,
                    cause="question-answered",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                _escalations_activity_stmt(self._store, deleted),
                ts_col=s.escalations.c.recorded_at,
                pk_col=s.escalations.c.id,
                since=since,
                limit=limit,
                chunks=chunks,
                builder=lambda r: ActivityEntry(
                    type="chunk-changed",
                    key=f"escalations:{r.id}",
                    at=r.recorded_at,
                    chunk_id=r.chunk_id,
                    cause="escalated",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                _requeues_activity_stmt(self._store, deleted),
                ts_col=s.requeues.c.requeued_at,
                pk_col=s.requeues.c.id,
                since=since,
                limit=limit,
                chunks=chunks,
                builder=lambda r: ActivityEntry(
                    type="chunk-changed",
                    key=f"requeues:{r.id}",
                    at=r.requeued_at,
                    chunk_id=r.chunk_id,
                    cause="requeued",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                _route_released_activity_stmt(self._store, deleted),
                ts_col=s.route_released.c.released_at,
                pk_col=s.route_released.c.id,
                since=since,
                limit=limit,
                chunks=chunks,
                builder=lambda r: ActivityEntry(
                    type="chunk-changed",
                    key=f"route_released:{r.id}",
                    at=r.released_at,
                    chunk_id=r.chunk_id,
                    cause="detached",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                _chunk_pause_facts_activity_stmt(self._store, deleted),
                ts_col=s.chunk_pause_facts.c.set_at,
                pk_col=s.chunk_pause_facts.c.id,
                since=since,
                limit=limit,
                chunks=chunks,
                builder=lambda r: ActivityEntry(
                    type="chunk-changed",
                    key=f"chunk_pause_facts:{r.id}",
                    at=r.set_at,
                    chunk_id=r.chunk_id,
                    cause="paused" if r.paused else "resumed",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                _chunk_stopped_activity_stmt(self._store, deleted),
                ts_col=s.chunk_stopped.c.stopped_at,
                pk_col=s.chunk_stopped.c.id,
                since=since,
                limit=limit,
                chunks=chunks,
                builder=lambda r: ActivityEntry(
                    type="chunk-changed",
                    key=f"chunk_stopped:{r.id}",
                    at=r.stopped_at,
                    chunk_id=r.chunk_id,
                    cause="stopped",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                _chunk_completed_activity_stmt(self._store, deleted),
                ts_col=s.chunk_completed.c.completed_at,
                pk_col=s.chunk_completed.c.id,
                since=since,
                limit=limit,
                chunks=chunks,
                builder=lambda r: ActivityEntry(
                    type="chunk-changed",
                    key=f"chunk_completed:{r.id}",
                    at=r.completed_at,
                    chunk_id=r.chunk_id,
                    cause="completed",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                _chunk_deleted_activity_stmt(self._store),
                ts_col=s.chunk_deleted.c.deleted_at,
                pk_col=s.chunk_deleted.c.id,
                since=since,
                limit=limit,
                chunks=chunks,
                builder=lambda r: ActivityEntry(
                    type="chunk-changed",
                    key=f"chunk_deleted:{r.id}",
                    at=r.deleted_at,
                    chunk_id=r.chunk_id,
                    cause="deleted",
                    graph_id=r.graph_id,
                    by=r.deleted_by,
                ),
            )
            return rows

    @staticmethod
    def _bounded(conn: Connection, stmt, *, ts_col, pk_col, since: datetime, limit: int, chunks, builder):  # type: ignore[no-untyped-def]
        """Run one source's own ``ORDER BY <ts> DESC, <pk> DESC LIMIT :limit`` bounded
        read and reshape each row via ``builder`` — the one piece every
        :meth:`activity_facts_since` source shares (never a full-table scan). ``chunks``, when given, narrows
        the source to the facts of those chunks: every source selects the chunk it names as ``chunk_id``."""
        if chunks is not None:
            stmt = stmt.where(stmt.selected_columns.chunk_id.in_(chunks))
        bounded_stmt = _bounded_stmt(stmt, ts_col=ts_col, pk_col=pk_col, since=since, limit=limit)
        return [builder(row) for row in conn.execute(bounded_stmt).all()]

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
        detail: dict | None,
        at: datetime,
    ) -> int:
        # Append-only operational fact, no epoch fence. `detail` serializes
        # to JSON text here; `chunk_id` is None for a runner-scoped event.
        with self._store.write("record_event") as conn:
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


def _conforms_events(x: ChunkEventsStore) -> IWriteChunkEventsRepository:
    return x
