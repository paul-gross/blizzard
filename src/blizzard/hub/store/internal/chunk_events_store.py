"""SQLAlchemy adapter for the chunk events seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``).

D6: ``activity_facts_since`` stays one bounded read per mapped fact table on one
connection, unchanged by the seam carve — it was already many single-table reads
folded into one ``with self._store.read(...)`` block before the carve, and stays that
shape now."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import Connection, select

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.events import IWriteChunkEventsRepository
from blizzard.hub.domain.work import DEFAULT_EVENT_LIST_LIMIT, ActivityRow, EventRow
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections

# The hub coordinator's own reserved ``transitions.runner_id`` (issue #213) — the only
# fact-table difference between a ``hub-advanced`` and a ``node-completed`` transition.
_HUB_RUNNER_ID = "hub"


class ChunkEventsStore:
    """The fleet's operational event log and the per-chunk change-activity feed."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def list_events(
        self,
        *,
        severity: str | None = None,
        runner_id: str | None = None,
        chunk_id: str | None = None,
        since: datetime | None = None,
        limit: int = DEFAULT_EVENT_LIST_LIMIT,
    ) -> list[EventRow]:
        with self._store.read("list_events") as conn:
            stmt = select(s.event_log)
            if severity is not None:
                stmt = stmt.where(s.event_log.c.severity == severity)
            if runner_id is not None:
                stmt = stmt.where(s.event_log.c.runner_id == runner_id)
            if chunk_id is not None:
                stmt = stmt.where(s.event_log.c.chunk_id == chunk_id)
            if since is not None:
                stmt = stmt.where(s.event_log.c.recorded_at >= since)
            stmt = stmt.order_by(s.event_log.c.recorded_at.desc(), s.event_log.c.id.desc()).limit(limit)
            return [
                EventRow(
                    id=row.id,
                    recorded_at=row.recorded_at,
                    severity=row.severity,
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

    def activity_facts_since(self, since: datetime, *, limit: int) -> list[ActivityRow]:
        """See
        :meth:`~blizzard.hub.domain.chunks.events.IReadChunkEventsRepository.activity_facts_since` — one
        bounded read per mapped ``ChunkChangeCause`` fact table, concatenated, unsorted across
        sources. Every per-chunk source joins ``chunks`` for its current ``graph_id``, except
        ``transitions``/``chunk_migrations``, which carry their own column."""
        with self._store.read("activity_facts_since") as conn:
            rows: list[ActivityRow] = []
            # Resolved once (issue #364): every fact-source block below excludes a
            # deleted chunk by referencing this same subquery, rather than repeating it.
            deleted = select(s.chunk_deleted.c.chunk_id)
            rows += self._bounded(
                conn,
                select(s.chunks.c.chunk_id, s.chunks.c.graph_id, s.chunks.c.minted_at).where(
                    s.chunks.c.chunk_id.not_in(deleted)
                ),
                ts_col=s.chunks.c.minted_at,
                pk_col=s.chunks.c.chunk_id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
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
                select(
                    s.chunk_promoted.c.id,
                    s.chunk_promoted.c.chunk_id,
                    s.chunk_promoted.c.promoted_at,
                    s.chunks.c.graph_id,
                )
                .select_from(s.chunk_promoted.join(s.chunks, s.chunks.c.chunk_id == s.chunk_promoted.c.chunk_id))
                .where(s.chunk_promoted.c.chunk_id.not_in(deleted)),
                ts_col=s.chunk_promoted.c.promoted_at,
                pk_col=s.chunk_promoted.c.id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
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
                select(
                    s.chunk_grouped.c.id, s.chunk_grouped.c.chunk_id, s.chunk_grouped.c.grouped_at, s.chunks.c.graph_id
                ).select_from(s.chunk_grouped.join(s.chunks, s.chunks.c.chunk_id == s.chunk_grouped.c.chunk_id)),
                ts_col=s.chunk_grouped.c.grouped_at,
                pk_col=s.chunk_grouped.c.id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
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
                select(
                    s.route_created.c.route_id,
                    s.route_created.c.chunk_id,
                    s.route_created.c.runner_id,
                    s.route_created.c.created_at,
                    s.chunks.c.graph_id,
                )
                .select_from(s.route_created.join(s.chunks, s.chunks.c.chunk_id == s.route_created.c.chunk_id))
                .where(s.route_created.c.chunk_id.not_in(deleted)),
                ts_col=s.route_created.c.created_at,
                pk_col=s.route_created.c.route_id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
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
                select(
                    s.transitions.c.transition_id,
                    s.transitions.c.chunk_id,
                    s.transitions.c.runner_id,
                    s.transitions.c.graph_id,
                    s.transitions.c.recorded_at,
                ).where(s.transitions.c.chunk_id.not_in(deleted)),
                ts_col=s.transitions.c.recorded_at,
                pk_col=s.transitions.c.transition_id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
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
                select(
                    s.chunk_migrations.c.migration_id,
                    s.chunk_migrations.c.chunk_id,
                    s.chunk_migrations.c.to_graph_id,
                    s.chunk_migrations.c.recorded_at,
                ).where(s.chunk_migrations.c.chunk_id.not_in(deleted)),
                ts_col=s.chunk_migrations.c.recorded_at,
                pk_col=s.chunk_migrations.c.migration_id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
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
                select(
                    s.chunk_restarts.c.id,
                    s.chunk_restarts.c.chunk_id,
                    s.chunk_restarts.c.recorded_at,
                    s.chunks.c.graph_id,
                )
                .select_from(s.chunk_restarts.join(s.chunks, s.chunks.c.chunk_id == s.chunk_restarts.c.chunk_id))
                .where(s.chunk_restarts.c.chunk_id.not_in(deleted)),
                ts_col=s.chunk_restarts.c.recorded_at,
                pk_col=s.chunk_restarts.c.id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
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
                select(
                    s.decisions.c.decision_id, s.decisions.c.chunk_id, s.decisions.c.submitted_at, s.chunks.c.graph_id
                )
                .select_from(s.decisions.join(s.chunks, s.chunks.c.chunk_id == s.decisions.c.chunk_id))
                .where(s.decisions.c.chunk_id.not_in(deleted)),
                ts_col=s.decisions.c.submitted_at,
                pk_col=s.decisions.c.decision_id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
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
                select(
                    s.decision_resolutions.c.decision_id,
                    s.decisions.c.chunk_id,
                    s.decision_resolutions.c.resolved_at,
                    s.chunks.c.graph_id,
                )
                .select_from(
                    s.decision_resolutions.join(
                        s.decisions, s.decisions.c.decision_id == s.decision_resolutions.c.decision_id
                    ).join(s.chunks, s.chunks.c.chunk_id == s.decisions.c.chunk_id)
                )
                .where(s.decisions.c.chunk_id.not_in(deleted)),
                ts_col=s.decision_resolutions.c.resolved_at,
                pk_col=s.decision_resolutions.c.decision_id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
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
                select(
                    s.questions.c.question_id,
                    s.questions.c.chunk_id,
                    s.questions.c.runner_id,
                    s.questions.c.asked_at,
                    s.chunks.c.graph_id,
                )
                .select_from(s.questions.join(s.chunks, s.chunks.c.chunk_id == s.questions.c.chunk_id))
                .where(s.questions.c.chunk_id.not_in(deleted)),
                ts_col=s.questions.c.asked_at,
                pk_col=s.questions.c.question_id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
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
                select(
                    s.question_answers.c.question_id,
                    s.questions.c.chunk_id,
                    s.question_answers.c.answered_at,
                    s.chunks.c.graph_id,
                )
                .select_from(
                    s.question_answers.join(
                        s.questions, s.questions.c.question_id == s.question_answers.c.question_id
                    ).join(s.chunks, s.chunks.c.chunk_id == s.questions.c.chunk_id)
                )
                .where(s.questions.c.chunk_id.not_in(deleted)),
                ts_col=s.question_answers.c.answered_at,
                pk_col=s.question_answers.c.question_id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
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
                select(s.escalations.c.id, s.escalations.c.chunk_id, s.escalations.c.recorded_at, s.chunks.c.graph_id)
                .select_from(s.escalations.join(s.chunks, s.chunks.c.chunk_id == s.escalations.c.chunk_id))
                .where(s.escalations.c.chunk_id.not_in(deleted)),
                ts_col=s.escalations.c.recorded_at,
                pk_col=s.escalations.c.id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
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
                select(s.requeues.c.id, s.requeues.c.chunk_id, s.requeues.c.requeued_at, s.chunks.c.graph_id)
                .select_from(s.requeues.join(s.chunks, s.chunks.c.chunk_id == s.requeues.c.chunk_id))
                .where(s.requeues.c.chunk_id.not_in(deleted)),
                ts_col=s.requeues.c.requeued_at,
                pk_col=s.requeues.c.id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
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
                select(
                    s.route_released.c.id,
                    s.route_released.c.chunk_id,
                    s.route_released.c.released_at,
                    s.chunks.c.graph_id,
                )
                .select_from(s.route_released.join(s.chunks, s.chunks.c.chunk_id == s.route_released.c.chunk_id))
                .where(s.route_released.c.chunk_id.not_in(deleted)),
                ts_col=s.route_released.c.released_at,
                pk_col=s.route_released.c.id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
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
                select(
                    s.chunk_pause_facts.c.id,
                    s.chunk_pause_facts.c.chunk_id,
                    s.chunk_pause_facts.c.paused,
                    s.chunk_pause_facts.c.set_at,
                    s.chunks.c.graph_id,
                )
                .select_from(s.chunk_pause_facts.join(s.chunks, s.chunks.c.chunk_id == s.chunk_pause_facts.c.chunk_id))
                .where(s.chunk_pause_facts.c.chunk_id.not_in(deleted)),
                ts_col=s.chunk_pause_facts.c.set_at,
                pk_col=s.chunk_pause_facts.c.id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
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
                select(
                    s.chunk_stopped.c.id, s.chunk_stopped.c.chunk_id, s.chunk_stopped.c.stopped_at, s.chunks.c.graph_id
                )
                .select_from(s.chunk_stopped.join(s.chunks, s.chunks.c.chunk_id == s.chunk_stopped.c.chunk_id))
                .where(s.chunk_stopped.c.chunk_id.not_in(deleted)),
                ts_col=s.chunk_stopped.c.stopped_at,
                pk_col=s.chunk_stopped.c.id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
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
                select(
                    s.chunk_completed.c.id,
                    s.chunk_completed.c.chunk_id,
                    s.chunk_completed.c.completed_at,
                    s.chunks.c.graph_id,
                )
                .select_from(s.chunk_completed.join(s.chunks, s.chunks.c.chunk_id == s.chunk_completed.c.chunk_id))
                .where(s.chunk_completed.c.chunk_id.not_in(deleted)),
                ts_col=s.chunk_completed.c.completed_at,
                pk_col=s.chunk_completed.c.id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
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
                select(
                    s.chunk_deleted.c.id,
                    s.chunk_deleted.c.chunk_id,
                    s.chunk_deleted.c.deleted_at,
                    s.chunk_deleted.c.deleted_by,
                    s.chunks.c.graph_id,
                ).select_from(s.chunk_deleted.join(s.chunks, s.chunks.c.chunk_id == s.chunk_deleted.c.chunk_id)),
                ts_col=s.chunk_deleted.c.deleted_at,
                pk_col=s.chunk_deleted.c.id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
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
    def _bounded(conn: Connection, stmt, *, ts_col, pk_col, since: datetime, limit: int, builder):  # type: ignore[no-untyped-def]
        """Run one source's own ``ORDER BY <ts> DESC, <pk> DESC LIMIT :limit`` bounded
        read and reshape each row via ``builder`` — the one piece every
        :meth:`activity_facts_since` source shares (issue #213, AC4: never a full-table
        scan)."""
        bounded_stmt = stmt.where(ts_col >= since).order_by(ts_col.desc(), pk_col.desc()).limit(limit)
        return [builder(row) for row in conn.execute(bounded_stmt).all()]

    def record_event(
        self,
        *,
        severity: str,
        kind: str,
        runner_id: str,
        chunk_id: str | None,
        lease_id: str | None,
        node_name: str | None,
        message: str,
        detail: dict | None,
        at: datetime,
    ) -> int:
        # Append-only operational fact (issue #125), no epoch fence. `detail` serializes
        # to JSON text here; `chunk_id` is None for a runner-scoped event.
        with self._store.write("record_event") as conn:
            result = conn.execute(
                s.event_log.insert().values(
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
