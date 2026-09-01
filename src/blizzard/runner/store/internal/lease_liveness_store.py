"""SQLAlchemy adapter for the lease-liveness repository seam (package-private,
blizzard#411 Phase 4).

Heartbeat and spawn facts — REAP's staleness baseline. :meth:`LeaseLivenessStore.record_spawn`
also opens/carries-forward the lease's transcript segment in the SAME transaction — a
cross-concept write D1 keeps inside this one ``store/internal/`` package."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from blizzard.foundation.ids import SEGMENT_PREFIX, Id
from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.leases import IWriteLeaseLivenessRepository
from blizzard.runner.store.internal.base import NO_NORMALIZER_VERSION, RunnerStoreConnections, enqueue_transcript_final
from blizzard.runner.store.schema import heartbeats, lease_context, lease_spawns, leases, transcript_segments

_log = get_logger("blizzard.runner.store")


class LeaseLivenessStore:
    """Read-write liveness adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    # --- reads --------------------------------------------------------------

    def latest_heartbeat(self, lease_id: str) -> datetime | None:
        stmt = select(func.max(heartbeats.c.beat_at)).where(heartbeats.c.lease_id == lease_id)
        with self._store.connect() as conn:
            value = conn.execute(stmt).scalar_one_or_none()
        return value

    def latest_spawn(self, lease_id: str) -> datetime | None:
        stmt = select(func.max(lease_spawns.c.spawned_at)).where(lease_spawns.c.lease_id == lease_id)
        with self._store.connect() as conn:
            value = conn.execute(stmt).scalar_one_or_none()
        return value

    def lease_generation(self, lease_id: str) -> int:
        stmt = select(func.count()).select_from(lease_spawns).where(lease_spawns.c.lease_id == lease_id)
        with self._store.connect() as conn:
            return int(conn.execute(stmt).scalar_one())

    # --- writes -------------------------------------------------------------

    def record_heartbeat(self, *, lease_id: str, beat_at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(heartbeats.insert().values(lease_id=lease_id, beat_at=beat_at))
        _log.debug("heartbeat recorded", lease_id=lease_id)

    def record_spawn(
        self, lease_id: str, *, pid: int, process_start_time: str, session_id: str, spawned_at: datetime
    ) -> None:
        with self._store.begin() as conn:
            conn.execute(
                leases.update()
                .where(leases.c.lease_id == lease_id)
                .values(pid=pid, process_start_time=process_start_time, session_id=session_id)
            )
            # One transaction with the in-place pid rewrite: the spawn generation and the process
            # it describes are one fact, and a crash between them would leave the two disagreeing.
            conn.execute(lease_spawns.insert().values(lease_id=lease_id, spawned_at=spawned_at))
            generation = int(
                conn.execute(
                    select(func.count()).select_from(lease_spawns).where(lease_spawns.c.lease_id == lease_id)
                ).scalar_one()
            )
            # Every start path reaching this transaction is a segment boundary (issue #246,
            # D1) — stamped here, not at the call sites, so a fourth can't miss it.
            context_row = conn.execute(
                select(leases.c.chunk_id, leases.c.epoch, lease_context.c.node_id)
                .select_from(leases.join(lease_context, leases.c.lease_id == lease_context.c.lease_id))
                .where(leases.c.lease_id == lease_id)
            ).one()
            # Carries a resumed session's cursor forward — the cross-lease case finds its
            # predecessor already finalized, so this reads regardless of finalization.
            prior_segment = conn.execute(
                select(transcript_segments)
                .where(transcript_segments.c.chunk_id == context_row.chunk_id)
                .where(transcript_segments.c.session_id == session_id)
                # `segment_id` tie-breaks `stamped_at` (`bzh:sql-portable`) — a same-instant
                # pair would otherwise pick nondeterministically across backends.
                .order_by(transcript_segments.c.stamped_at.desc(), transcript_segments.c.segment_id.desc())
                .limit(1)
            ).one_or_none()
            carried_cursor: str | None = None
            if prior_segment is not None:
                carried_cursor = str(prior_segment.cursor) if prior_segment.cursor is not None else None
                if prior_segment.finalized_at is None:
                    conn.execute(
                        transcript_segments.update()
                        .where(transcript_segments.c.segment_id == prior_segment.segment_id)
                        .values(finalized_at=spawned_at)
                    )
                    enqueue_transcript_final(conn, prior_segment, at=spawned_at)
            conn.execute(
                transcript_segments.insert().values(
                    segment_id=Id.mint_at(SEGMENT_PREFIX, spawned_at).value,
                    chunk_id=str(context_row.chunk_id),
                    node_id=str(context_row.node_id),
                    epoch=int(context_row.epoch),
                    generation=generation,
                    lease_id=lease_id,
                    session_id=session_id,
                    cursor=carried_cursor,
                    shipped_bytes=0,
                    shipped_turns=0,
                    normalizer_version=NO_NORMALIZER_VERSION,
                    harness_version=None,
                    truncated_reason=None,
                    shipping_stopped_reason=None,
                    finalized_at=None,
                    stamped_at=spawned_at,
                )
            )
        _log.info("worker spawned", lease_id=lease_id, pid=pid, session_id=session_id)


def _conforms_lease_liveness_store(x: LeaseLivenessStore) -> IWriteLeaseLivenessRepository:
    return x
