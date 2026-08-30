"""SQLAlchemy adapter for the lease repository seam (package-private, blizzard#410).

Mint, spawn, heartbeat, closure, session, epoch, and preamble facts.
:meth:`LeaseStore.record_spawn`/:meth:`LeaseStore.record_closure` also finalize the
lease's open transcript segments in the SAME transaction — a cross-concept write D1
keeps inside this one ``store/internal/`` package."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, func, or_, select

from blizzard.foundation.ids import SEGMENT_PREFIX, Id
from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.leases import ClosedLeaseRecord, IWriteLeaseRepository, LeaseRecord, NewLease, PoolHead
from blizzard.runner.harness.fingerprint import PreambleFingerprint
from blizzard.runner.store.internal.base import (
    NO_NORMALIZER_VERSION,
    OPEN_INTENT,
    RunnerStoreConnections,
    Unclosed,
    enqueue_transcript_final,
    lease_select,
    row_to_lease,
)
from blizzard.runner.store.schema import (
    heartbeats,
    lease_closures,
    lease_context,
    lease_spawns,
    leases,
    outbound_buffer,
    resume_clears,
    resume_intents,
    session_ends,
    session_preamble_facts,
    takeovers,
    transcript_segments,
    usage_facts,
)

_log = get_logger("blizzard.runner.store")

# The closure reason an attempt an operator's restart superseded carries (issue #370) —
# read back to keep that attempt out of the node's retry budget.
_PREEMPTED_REASON = "preempted"

# Pinned by tests/test_pin_runner_store.py::test_a_rebind_after_a_release_reads_as_held's
# sibling lease cases.
OPEN_LEASE = Unclosed(leases.c.lease_id, lease_closures.c.lease_id)


class LeaseStore:
    """Read-write lease adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    # --- reads --------------------------------------------------------------

    def list_active_leases(self) -> list[LeaseRecord]:
        stmt = lease_select().where(OPEN_LEASE.clause)
        return [row_to_lease(r) for r in self._store.all(stmt)]

    def active_lease_for_chunk(self, chunk_id: str) -> LeaseRecord | None:
        stmt = (
            lease_select()
            .where(leases.c.chunk_id == chunk_id)
            .where(OPEN_LEASE.clause)
            .order_by(leases.c.created_at.desc())
        )
        rows = self._store.all(stmt)
        return row_to_lease(rows[0]) if rows else None

    def active_lease(self, lease_id: str) -> LeaseRecord | None:
        stmt = lease_select().where(leases.c.lease_id == lease_id).where(OPEN_LEASE.clause)
        rows = self._store.all(stmt)
        return row_to_lease(rows[0]) if rows else None

    def latest_lease_for_chunk(self, chunk_id: str) -> LeaseRecord | None:
        stmt = lease_select().where(leases.c.chunk_id == chunk_id).order_by(leases.c.created_at.desc())
        rows = self._store.all(stmt)
        return row_to_lease(rows[0]) if rows else None

    def latest_session_id(self, chunk_id: str, node_name: str | None) -> str | None:
        stmt = lease_select().where(leases.c.chunk_id == chunk_id).where(leases.c.session_id.is_not(None))
        if node_name is not None:
            stmt = stmt.where(lease_context.c.node_name == node_name)
        stmt = stmt.order_by(leases.c.created_at.desc(), leases.c.lease_id.desc())
        rows = self._store.all(stmt)
        return str(rows[0].session_id) if rows else None

    def pool_head(self, chunk_id: str, session_name: str) -> PoolHead | None:
        """The newest session-bearing lease stamping ``session_name`` — the pool's head.

        Same ordering and same session-bearing filter as :meth:`latest_session_id`,
        keyed on the stamped pool name rather than the node name."""
        stmt = (
            lease_select()
            .where(leases.c.chunk_id == chunk_id)
            .where(leases.c.session_id.is_not(None))
            .where(lease_context.c.session_name == session_name)
            .order_by(leases.c.created_at.desc(), leases.c.lease_id.desc())
        )
        rows = self._store.all(stmt)
        if not rows:
            return None
        row = rows[0]
        return PoolHead(
            session_id=str(row.session_id),
            lease_id=str(row.lease_id),
            resolved_model=row.resolved_model,
            resolved_effort=row.resolved_effort,
        )

    def session_invocation_count(self, session_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(usage_facts)
            .join(leases, leases.c.lease_id == usage_facts.c.lease_id)
            .where(leases.c.session_id == session_id)
        )
        rows = self._store.all(stmt)
        return int(rows[0][0]) if rows else 0

    def lease_for_session(self, session_id: str) -> LeaseRecord | None:
        """The newest lease that ran ``session_id`` — same ordering as `pool_head`."""
        stmt = (
            lease_select()
            .where(leases.c.session_id == session_id)
            .order_by(leases.c.created_at.desc(), leases.c.lease_id.desc())
        )
        rows = self._store.all(stmt)
        return row_to_lease(rows[0]) if rows else None

    def lease(self, lease_id: str) -> LeaseRecord | None:
        stmt = lease_select().where(leases.c.lease_id == lease_id)
        rows = self._store.all(stmt)
        return row_to_lease(rows[0]) if rows else None

    def list_closed_leases(self, limit: int) -> list[ClosedLeaseRecord]:
        stmt = (
            lease_select()
            .add_columns(lease_closures.c.reason, lease_closures.c.closed_at)
            .join(lease_closures, lease_closures.c.lease_id == leases.c.lease_id)
            .order_by(lease_closures.c.closed_at.desc())
            .limit(limit)
        )
        return [
            ClosedLeaseRecord(lease=row_to_lease(r), reason=str(r.reason), closed_at=r.closed_at)
            for r in self._store.all(stmt)
        ]

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

    def attempt_count(self, chunk_id: str, node_id: str) -> int:
        # A preempted attempt was superseded, not spent (issue #370): counting it would carry
        # the node toward exhaustion and escalate the very chunk the operator is rescuing.
        preempted = select(lease_closures.c.lease_id).where(lease_closures.c.reason == _PREEMPTED_REASON)
        stmt = (
            select(func.count())
            .select_from(lease_context)
            .where(and_(lease_context.c.chunk_id == chunk_id, lease_context.c.node_id == node_id))
            .where(lease_context.c.lease_id.not_in(preempted))
        )
        with self._store.connect() as conn:
            return int(conn.execute(stmt).scalar_one())

    def latest_epoch(self, chunk_id: str) -> int:
        lease_stmt = select(func.max(leases.c.epoch)).where(leases.c.chunk_id == chunk_id)
        # A forced takeover's fence bump (issue #52) mints no local lease, so it is folded
        # in here alongside the lease-minted epochs.
        fence_stmt = select(func.max(takeovers.c.fence_epoch)).where(takeovers.c.chunk_id == chunk_id)
        with self._store.connect() as conn:
            lease_max = conn.execute(lease_stmt).scalar_one_or_none()
            fence_max = conn.execute(fence_stmt).scalar_one_or_none()
        return max(int(lease_max) if lease_max is not None else 0, int(fence_max) if fence_max is not None else 0)

    def session_preamble_fingerprint(self, session_id: str) -> PreambleFingerprint | None:
        # Ordered on the autoincrement pk, not on `recorded_at` or implicit insert order
        # (`bzh:sql-portable`).
        rows = self._store.all(
            select(session_preamble_facts.c.blizzard_digest, session_preamble_facts.c.workspace_digest)
            .where(session_preamble_facts.c.session_id == session_id)
            .order_by(session_preamble_facts.c.id.desc())
            .limit(1)
        )
        if not rows:
            return None
        return PreambleFingerprint(blizzard=str(rows[0].blizzard_digest), workspace=str(rows[0].workspace_digest))

    def resume_intent_lease_ids(self) -> set[str]:
        stmt = select(resume_intents.c.lease_id).where(OPEN_INTENT.clause).distinct()
        return {str(r.lease_id) for r in self._store.all(stmt)}

    def session_ended_lease_ids(self) -> set[str]:
        newest_spawn = (
            select(lease_spawns.c.lease_id, func.max(lease_spawns.c.spawned_at).label("spawned_at"))
            .group_by(lease_spawns.c.lease_id)
            .subquery()
        )
        stmt = (
            select(session_ends.c.lease_id)
            .select_from(session_ends.outerjoin(newest_spawn, newest_spawn.c.lease_id == session_ends.c.lease_id))
            # No spawn fact: fall back to the unscoped reading, which over-reports
            # "declared done" and so can only suppress a resume, never invent one.
            .where(or_(newest_spawn.c.spawned_at.is_(None), session_ends.c.ended_at >= newest_spawn.c.spawned_at))
            .distinct()
        )
        return {str(r.lease_id) for r in self._store.all(stmt)}

    def lease_generation(self, lease_id: str) -> int:
        stmt = select(func.count()).select_from(lease_spawns).where(lease_spawns.c.lease_id == lease_id)
        with self._store.connect() as conn:
            return int(conn.execute(stmt).scalar_one())

    def lease_ids_for_chunk(self, chunk_id: str) -> list[str]:
        stmt = select(leases.c.lease_id).where(leases.c.chunk_id == chunk_id)
        return [str(r.lease_id) for r in self._store.all(stmt)]

    # --- writes -------------------------------------------------------------

    def record_lease(self, lease: NewLease) -> None:
        with self._store.begin() as conn:
            conn.execute(
                leases.insert().values(
                    lease_id=lease.lease_id,
                    chunk_id=lease.chunk_id,
                    epoch=lease.epoch,
                    runner_id=lease.runner_id,
                    created_at=lease.created_at,
                )
            )
            conn.execute(
                lease_context.insert().values(
                    lease_id=lease.lease_id,
                    chunk_id=lease.chunk_id,
                    graph_id=lease.graph_id,
                    node_id=lease.node_id,
                    node_name=lease.node_name,
                    retries_max=lease.retries_max,
                    session_name=lease.session_name,
                    resolved_model=lease.resolved_model,
                    resolved_effort=lease.resolved_effort,
                    resolved_compaction_window=lease.resolved_compaction_window,
                    recorded_at=lease.created_at,
                )
            )
        _log.info(
            "lease minted", lease_id=lease.lease_id, chunk_id=lease.chunk_id, node=lease.node_name, epoch=lease.epoch
        )

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

    def record_heartbeat(self, *, lease_id: str, beat_at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(heartbeats.insert().values(lease_id=lease_id, beat_at=beat_at))
        _log.debug("heartbeat recorded", lease_id=lease_id)

    def record_closure(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        reason: str,
        closed_at: datetime,
        event_kind: str | None = None,
        event_payload: str | None = None,
    ) -> int | None:
        # The closure and its operational event land in ONE transaction, so a `kill -9`
        # can neither surface an event for a closure that never happened nor drop one (#125).
        event_seq: int | None = None
        with self._store.begin() as conn:
            conn.execute(
                lease_closures.insert().values(
                    lease_id=lease_id, chunk_id=chunk_id, node_id=node_id, reason=reason, closed_at=closed_at
                )
            )
            if event_kind is not None and event_payload is not None:
                result = conn.execute(
                    outbound_buffer.insert().values(
                        kind=event_kind,
                        chunk_id=chunk_id,
                        lease_id=lease_id,
                        payload=event_payload,
                        created_at=closed_at,
                    )
                )
                key = result.inserted_primary_key
                event_seq = int(key[0]) if key is not None else 0
            # Segments are final by step close (issue #246) — finalized atomically here, on
            # the transcript lane's OWN buffer (D3), never `outbound_buffer` above.
            open_segments = conn.execute(
                select(transcript_segments)
                .where(transcript_segments.c.lease_id == lease_id)
                .where(transcript_segments.c.finalized_at.is_(None))
            ).all()
            for segment in open_segments:
                conn.execute(
                    transcript_segments.update()
                    .where(transcript_segments.c.segment_id == segment.segment_id)
                    .values(finalized_at=closed_at)
                )
                enqueue_transcript_final(conn, segment, at=closed_at)
        _log.info(
            "lease closed",
            lease_id=lease_id,
            chunk_id=chunk_id,
            reason=reason,
            transcript_segments_finalized=len(open_segments),
        )
        return event_seq

    def record_resume_intent(self, *, lease_id: str, marked_at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(resume_intents.insert().values(lease_id=lease_id, marked_at=marked_at))
        _log.info("resume intent marked", lease_id=lease_id)

    def record_resume_clear(self, *, lease_id: str, cleared_at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(resume_clears.insert().values(lease_id=lease_id, cleared_at=cleared_at))
        _log.info("resume intent cleared", lease_id=lease_id)

    def record_session_end(self, *, lease_id: str, ended_at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(session_ends.insert().values(lease_id=lease_id, ended_at=ended_at))
        _log.info("session end recorded", lease_id=lease_id)

    def record_session_preamble(self, session_id: str, *, fingerprint: PreambleFingerprint, at: datetime) -> None:
        # A plain append, no check-then-insert: a per-spawn fact whose newest row is the
        # answer, not a once-per-key guard.
        with self._store.begin() as conn:
            conn.execute(
                session_preamble_facts.insert().values(
                    session_id=session_id,
                    blizzard_digest=fingerprint.blizzard,
                    workspace_digest=fingerprint.workspace,
                    recorded_at=at,
                )
            )
        _log.info("session preamble recorded", session_id=session_id)

    # ``lease_select``/``row_to_lease`` moved to ``store/internal/base.py`` (blizzard#410):
    # the transcripts ledger's backfill read also joins a lease.


def _conforms_lease_store(x: LeaseStore) -> IWriteLeaseRepository:
    return x
