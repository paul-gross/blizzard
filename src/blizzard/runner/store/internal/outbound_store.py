"""SQLAlchemy adapter for the outbound-buffer repository seam (package-private)."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_

from blizzard.runner.hub.outbound_buffer import (
    SUBMISSION_KINDS,
    BufferedFact,
    IWriteOutboundRepository,
    OutboundFactEntry,
)
from blizzard.runner.leases.lease_lock import ILeaseUnit
from blizzard.runner.store.internal.base import insert_outbox_row, unit_conn
from blizzard.runner.store.schema import lease_closures, outbound_buffer
from blizzard.runner.store.scoped import RunnerStoreConnections

# See IWriteOutboundRepository.prune_outbound's own docstring for the retention contract.
_OUTBOUND_RETENTION_WINDOW = timedelta(days=7)


class OutboundStore:
    """Read-write outbound-buffer adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def pending_submission_lease_ids(self) -> set[str]:
        stmt = self._store.select(outbound_buffer, outbound_buffer.c.lease_id).where(
            and_(
                outbound_buffer.c.acked_at.is_(None),
                outbound_buffer.c.kind.in_(SUBMISSION_KINDS),
                outbound_buffer.c.lease_id.is_not(None),
            )
        )
        return {str(r.lease_id) for r in self._store.all(stmt)}

    def submission_buffered(self, lease_id: str, *, unit: ILeaseUnit | None = None) -> bool:
        stmt = self._store.select(outbound_buffer, outbound_buffer.c.seq).where(
            and_(outbound_buffer.c.lease_id == lease_id, outbound_buffer.c.kind.in_(SUBMISSION_KINDS))
        )
        rows = self._store.all(stmt.limit(1)) if unit is None else unit_conn(unit).execute(stmt.limit(1)).all()
        return bool(rows)

    def pending_outbound(self, *, limit: int | None = None) -> list[BufferedFact]:
        stmt = (
            self._store.select(outbound_buffer)
            .where(outbound_buffer.c.acked_at.is_(None))
            .order_by(outbound_buffer.c.seq)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return [
            BufferedFact(
                seq=int(r.seq),
                kind=str(r.kind),
                chunk_id=str(r.chunk_id) if r.chunk_id is not None else None,
                lease_id=str(r.lease_id) if r.lease_id is not None else None,
                payload=str(r.payload),
                created_at=r.created_at,
            )
            for r in self._store.all(stmt)
        ]

    def pending_outbound_count(self) -> int:
        stmt = self._store.select(outbound_buffer, func.count()).where(outbound_buffer.c.acked_at.is_(None))
        rows = self._store.all(stmt)
        return int(rows[0][0]) if rows else 0

    def recent_outbound(self, limit: int) -> list[OutboundFactEntry]:
        stmt = self._store.select(outbound_buffer).order_by(outbound_buffer.c.seq.desc()).limit(limit)
        return [
            OutboundFactEntry(
                seq=int(r.seq),
                kind=str(r.kind),
                chunk_id=str(r.chunk_id) if r.chunk_id is not None else None,
                lease_id=str(r.lease_id) if r.lease_id is not None else None,
                created_at=r.created_at,
                acked_at=r.acked_at,
            )
            for r in self._store.all(stmt)
        ]

    def enqueue_outbound(
        self,
        *,
        kind: str,
        chunk_id: str | None,
        lease_id: str | None,
        payload: str,
        created_at: datetime,
        unit: ILeaseUnit | None = None,
    ) -> int:
        values = {
            "kind": kind,
            "chunk_id": chunk_id,
            "lease_id": lease_id,
            "payload": payload,
            "created_at": created_at,
        }
        if unit is not None:
            return insert_outbox_row(self._store, unit_conn(unit), outbound_buffer, **values)
        with self._store.begin() as conn:
            return insert_outbox_row(self._store, conn, outbound_buffer, **values)

    def ack_outbound(self, seq: int, *, acked_at: datetime) -> None:
        self.ack_outbound_batch([seq], acked_at=acked_at)

    def ack_outbound_batch(self, seqs: list[int], *, acked_at: datetime) -> None:
        # Only `acked_at IS NULL` rows take the update (`OUTBOUND_TRANSITIONS`).
        with self._store.begin() as conn:
            conn.execute(
                self._store.update(outbound_buffer)
                .where(and_(outbound_buffer.c.seq.in_(seqs), outbound_buffer.c.acked_at.is_(None)))
                .values(acked_at=acked_at)
            )

    def prune_outbound(self, *, now: datetime) -> int:
        cutoff = now - _OUTBOUND_RETENTION_WINDOW
        with self._store.begin() as conn:
            # Read in the SAME transaction as the delete below, so a fact enqueued between
            # the two can never be missed as "no pending row" and pruned as if above none.
            floor = conn.execute(
                self._store.select(outbound_buffer, func.min(outbound_buffer.c.seq)).where(
                    outbound_buffer.c.acked_at.is_(None)
                )
            ).scalar_one()
            # A submission row stays while its lease has no closure: admission reads it as "the outcome is
            # settled", so pruning it would reopen the lease to declarations nothing would carry.
            stmt = self._store.delete(outbound_buffer).where(
                and_(
                    outbound_buffer.c.acked_at.is_not(None),
                    outbound_buffer.c.acked_at < cutoff,
                    or_(
                        outbound_buffer.c.kind.not_in(SUBMISSION_KINDS),
                        outbound_buffer.c.lease_id.is_(None),
                        self._store.exists(lease_closures, lease_closures.c.lease_id == outbound_buffer.c.lease_id),
                    ),
                )
            )
            if floor is not None:
                stmt = stmt.where(outbound_buffer.c.seq < floor)
            result = conn.execute(stmt)
        return result.rowcount


_outbound_store_conforms: type[IWriteOutboundRepository] = OutboundStore
