"""SQLAlchemy adapter for the lease-record repository seam (package-private,
blizzard#411 Phase 4).

Mint, closure, and lookups by lease or chunk identity. :meth:`LeaseRecordStore.record_closure`
also finalizes the lease's open transcript segments in the SAME transaction — a
cross-concept write D1 keeps inside this one ``store/internal/`` package."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, func, select

from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.leases import ClosedLeaseRecord, IWriteLeaseRecordRepository, LeaseRecord, NewLease
from blizzard.runner.store.internal.base import (
    RunnerStoreConnections,
    Unclosed,
    enqueue_transcript_final,
    lease_select,
    row_to_lease,
)
from blizzard.runner.store.schema import (
    lease_closures,
    lease_context,
    leases,
    outbound_buffer,
    takeovers,
    transcript_segments,
)

_log = get_logger("blizzard.runner.store")

# The closure reason an attempt an operator's restart superseded carries (issue #370) —
# read back to keep that attempt out of the node's retry budget.
_PREEMPTED_REASON = "preempted"

# Pinned by tests/test_pin_runner_store.py::test_a_rebind_after_a_release_reads_as_held's
# sibling lease cases.
_OPEN_LEASE = Unclosed(leases.c.lease_id, lease_closures.c.lease_id)


class LeaseRecordStore:
    """Read-write lease-identity adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    # --- reads --------------------------------------------------------------

    def list_active_leases(self) -> list[LeaseRecord]:
        stmt = lease_select().where(_OPEN_LEASE.clause)
        return [row_to_lease(r) for r in self._store.all(stmt)]

    def active_lease_for_chunk(self, chunk_id: str) -> LeaseRecord | None:
        stmt = (
            lease_select()
            .where(leases.c.chunk_id == chunk_id)
            .where(_OPEN_LEASE.clause)
            .order_by(leases.c.created_at.desc())
        )
        rows = self._store.all(stmt)
        return row_to_lease(rows[0]) if rows else None

    def active_lease(self, lease_id: str) -> LeaseRecord | None:
        stmt = lease_select().where(leases.c.lease_id == lease_id).where(_OPEN_LEASE.clause)
        rows = self._store.all(stmt)
        return row_to_lease(rows[0]) if rows else None

    def latest_lease_for_chunk(self, chunk_id: str) -> LeaseRecord | None:
        stmt = lease_select().where(leases.c.chunk_id == chunk_id).order_by(leases.c.created_at.desc())
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


def _conforms_lease_record_store(x: LeaseRecordStore) -> IWriteLeaseRecordRepository:
    return x
