"""SQLAlchemy adapter for the lease-liveness repository seam (package-private).

Heartbeat and spawn facts — REAP's staleness baseline. :meth:`LeaseLivenessStore.record_spawn`
also opens/carries-forward the lease's transcript segment in the SAME transaction — a
cross-concept write D1 keeps inside this one ``store/internal/`` package."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import Connection, and_, func, select

from blizzard.foundation.ids import SEGMENT_PREFIX, Id
from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.leases import IWriteLeaseLivenessRepository
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.store.internal.base import NO_NORMALIZER_VERSION, RunnerStoreConnections, enqueue_transcript_final
from blizzard.runner.store.schema import heartbeats, lease_context, lease_spawns, leases, transcript_segments

_log = get_logger("blizzard.runner.store")

# See IWriteLeaseLivenessRepository.prune_heartbeats's own docstring for the retention contract.
_HEARTBEAT_RETENTION_WINDOW = timedelta(days=1)


def _lease_generation(conn: Connection, lease_id: str) -> int:
    """This lease's spawn generation as of the row just written in THIS transaction —
    shared by :meth:`~LeaseLivenessStore.record_spawn`'s single-shot write and
    :meth:`~LeaseLivenessStore.record_identified_spawn`'s phase-two write, since either one
    inserts (or, for the two-phase path, already inserted at phase one) exactly one
    ``lease_spawns`` row per generation regardless of whether it identifies successfully."""
    return int(
        conn.execute(
            select(func.count()).select_from(lease_spawns).where(lease_spawns.c.lease_id == lease_id)
        ).scalar_one()
    )


def _open_provisional_spawn_id(conn: Connection, lease_id: str, *, required: bool = True) -> int | None:
    """The newest ``lease_spawns`` row for ``lease_id`` with no ``session_id`` yet —
    phase one's own row, found by :meth:`~LeaseLivenessStore.record_identified_spawn` and
    :meth:`~LeaseLivenessStore.record_identity_failed` to close it one way or the other.
    ``session_id IS NULL`` alone can't tell that apart from a pre-two-phase legacy row (an
    additive migration, no backfill) — requiring ``pid`` non-NULL too is what does."""
    row = conn.execute(
        select(lease_spawns.c.id)
        .where(
            lease_spawns.c.lease_id == lease_id,
            lease_spawns.c.session_id.is_(None),
            lease_spawns.c.pid.isnot(None),
        )
        # `id` breaks no tie here — it IS the ordering fact (`bzh:sql-portable`): insertion
        # order, not a timestamp, is what "newest provisional generation" means.
        .order_by(lease_spawns.c.id.desc())
        .limit(1)
    ).one_or_none()
    if row is None:
        if required:
            raise ValueError(f"lease {lease_id} has no open provisional spawn generation to identify")
        return None
    return int(row.id)


def _open_transcript_segment(
    conn: Connection, *, lease_id: str, session: SessionReference, generation: int, at: datetime
) -> None:
    """Open this generation's transcript segment, carrying a resumed session's cursor
    forward and finalizing its predecessor — shared by :meth:`~LeaseLivenessStore.record_spawn`
    and :meth:`~LeaseLivenessStore.record_identified_spawn`. Every start path is a segment
    boundary (issue #246, D1), stamped here so a future write can't miss it."""
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
        .where(transcript_segments.c.session_id == session.session_id)
        .where(transcript_segments.c.harness_id == session.harness_id)
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
                .values(finalized_at=at)
            )
            enqueue_transcript_final(conn, prior_segment, at=at)
    conn.execute(
        transcript_segments.insert().values(
            segment_id=Id.mint_at(SEGMENT_PREFIX, at).value,
            chunk_id=str(context_row.chunk_id),
            node_id=str(context_row.node_id),
            epoch=int(context_row.epoch),
            generation=generation,
            lease_id=lease_id,
            session_id=session.session_id,
            harness_id=session.harness_id,
            cursor=carried_cursor,
            shipped_bytes=0,
            shipped_turns=0,
            normalizer_version=NO_NORMALIZER_VERSION,
            harness_version=None,
            truncated_reason=None,
            shipping_stopped_reason=None,
            finalized_at=None,
            stamped_at=at,
        )
    )


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

    def prune_heartbeats(self, *, now: datetime) -> int:
        cutoff = now - _HEARTBEAT_RETENTION_WINDOW
        newest_beat = heartbeats.alias("newest_beat")
        latest_beat = (
            select(func.max(newest_beat.c.beat_at))
            .where(newest_beat.c.lease_id == heartbeats.c.lease_id)
            .scalar_subquery()
        )
        with self._store.begin() as conn:
            # `< latest_beat` (never `<=`) keeps EVERY row tied for newest — a same-instant
            # pair is not a superseded beat, so neither is pruned out from under the other.
            result = conn.execute(
                heartbeats.delete().where(and_(heartbeats.c.beat_at < cutoff, heartbeats.c.beat_at < latest_beat))
            )
        return result.rowcount

    def record_spawn(
        self,
        lease_id: str,
        *,
        pid: int,
        process_start_time: str,
        spawned_at: datetime,
        session: SessionReference,
        harness_version: str | None = None,
        pgid: int | None = None,
    ) -> None:
        with self._store.begin() as conn:
            conn.execute(
                leases.update()
                .where(leases.c.lease_id == lease_id)
                .values(
                    pid=pid,
                    process_start_time=process_start_time,
                    session_id=session.session_id,
                    harness_id=session.harness_id,
                    # `pgid` defaults `None` (D3): an unknowing caller must not leave a
                    # PRIOR generation's stale group standing rather than reading "unknown".
                    pgid=pgid,
                )
            )
            # One transaction with the in-place pid rewrite: the spawn generation and the process
            # it describes are one fact, and a crash between them would leave the two disagreeing.
            conn.execute(
                lease_spawns.insert().values(
                    lease_id=lease_id,
                    spawned_at=spawned_at,
                    harness_id=session.harness_id,
                    harness_version=harness_version,
                    pid=pid,
                    process_start_time=process_start_time,
                    pgid=pgid,
                    session_id=session.session_id,
                    identified_at=spawned_at,
                )
            )
            generation = _lease_generation(conn, lease_id)
            _open_transcript_segment(conn, lease_id=lease_id, session=session, generation=generation, at=spawned_at)
        _log.info(
            "worker spawned",
            lease_id=lease_id,
            pid=pid,
            session_id=session.session_id,
            harness_id=session.harness_id,
        )

    def record_provisional_spawn(
        self,
        lease_id: str,
        *,
        pid: int,
        process_start_time: str,
        pgid: int | None,
        spawned_at: datetime,
        harness_id: str,
    ) -> None:
        with self._store.begin() as conn:
            conn.execute(
                leases.update()
                .where(leases.c.lease_id == lease_id)
                .values(pid=pid, process_start_time=process_start_time, pgid=pgid)
            )
            conn.execute(
                lease_spawns.insert().values(
                    lease_id=lease_id,
                    spawned_at=spawned_at,
                    harness_id=harness_id,
                    pid=pid,
                    process_start_time=process_start_time,
                    pgid=pgid,
                )
            )
        _log.info("worker launched — provisional, identity not yet known", lease_id=lease_id, pid=pid, pgid=pgid)

    def record_identified_spawn(
        self,
        lease_id: str,
        *,
        session: SessionReference,
        identified_at: datetime,
        harness_version: str | None = None,
    ) -> None:
        with self._store.begin() as conn:
            provisional_id = _open_provisional_spawn_id(conn, lease_id)
            conn.execute(
                lease_spawns.update()
                .where(lease_spawns.c.id == provisional_id)
                .values(session_id=session.session_id, harness_version=harness_version, identified_at=identified_at)
            )
            conn.execute(
                leases.update()
                .where(leases.c.lease_id == lease_id)
                .values(session_id=session.session_id, harness_id=session.harness_id)
            )
            generation = _lease_generation(conn, lease_id)
            _open_transcript_segment(conn, lease_id=lease_id, session=session, generation=generation, at=identified_at)
        _log.info(
            "worker identified",
            lease_id=lease_id,
            session_id=session.session_id,
            harness_id=session.harness_id,
        )

    def record_identity_failed(self, lease_id: str, *, at: datetime) -> None:
        with self._store.begin() as conn:
            provisional_id = _open_provisional_spawn_id(conn, lease_id, required=False)
            if provisional_id is None:
                return
            conn.execute(lease_spawns.update().where(lease_spawns.c.id == provisional_id).values(identity_failed_at=at))
        _log.info("worker identity failed — provisional generation closed unidentified", lease_id=lease_id)


def _conforms_lease_liveness_store(x: LeaseLivenessStore) -> IWriteLeaseLivenessRepository:
    return x
