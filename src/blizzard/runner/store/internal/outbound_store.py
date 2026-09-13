"""SQLAlchemy adapter for the outbound-buffer repository seam (package-private, blizzard#410)."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, func, select

from blizzard.runner.domain.outbound import BufferedFact, IWriteOutboundRepository, OutboundFactRecord
from blizzard.runner.store.internal.base import RunnerStoreConnections
from blizzard.runner.store.schema import outbound_buffer

# Retention (Decision 4, issue #520): an acked row survives at least this long, so a hub
# outage this short never costs `recent_outbound`'s own week of local fact-log history.
_OUTBOUND_RETENTION_WINDOW = timedelta(days=7)


class OutboundStore:
    """Read-write outbound-buffer adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def pending_submission_lease_ids(self) -> set[str]:
        stmt = select(outbound_buffer.c.lease_id).where(
            and_(
                outbound_buffer.c.acked_at.is_(None),
                outbound_buffer.c.kind.in_(("completion.submitted", "decision.submitted")),
                outbound_buffer.c.lease_id.is_not(None),
            )
        )
        return {str(r.lease_id) for r in self._store.all(stmt)}

    def pending_outbound(self, limit: int) -> list[BufferedFact]:
        stmt = (
            select(outbound_buffer)
            .where(outbound_buffer.c.acked_at.is_(None))
            .order_by(outbound_buffer.c.seq)
            .limit(limit)
        )
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
        stmt = select(func.count()).select_from(outbound_buffer).where(outbound_buffer.c.acked_at.is_(None))
        rows = self._store.all(stmt)
        return int(rows[0][0]) if rows else 0

    def recent_outbound(self, limit: int) -> list[OutboundFactRecord]:
        stmt = select(outbound_buffer).order_by(outbound_buffer.c.seq.desc()).limit(limit)
        return [
            OutboundFactRecord(
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
        self, *, kind: str, chunk_id: str | None, lease_id: str | None, payload: str, created_at: datetime
    ) -> int:
        with self._store.begin() as conn:
            result = conn.execute(
                outbound_buffer.insert().values(
                    kind=kind, chunk_id=chunk_id, lease_id=lease_id, payload=payload, created_at=created_at
                )
            )
        key = result.inserted_primary_key
        return int(key[0]) if key is not None else 0

    def ack_outbound(self, seq: int, *, acked_at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(outbound_buffer.update().where(outbound_buffer.c.seq == seq).values(acked_at=acked_at))

    def ack_outbound_batch(self, seqs: list[int], *, acked_at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(outbound_buffer.update().where(outbound_buffer.c.seq.in_(seqs)).values(acked_at=acked_at))

    def prune_outbound(self, *, now: datetime) -> int:
        cutoff = now - _OUTBOUND_RETENTION_WINDOW
        with self._store.begin() as conn:
            # The pending floor, read in the SAME transaction as the delete below — a fact
            # enqueued between the two would otherwise risk being read as "no pending row"
            # and pruning an acked row that ought to have stayed below it.
            floor = conn.execute(
                select(func.min(outbound_buffer.c.seq)).where(outbound_buffer.c.acked_at.is_(None))
            ).scalar_one()
            stmt = outbound_buffer.delete().where(
                and_(outbound_buffer.c.acked_at.is_not(None), outbound_buffer.c.acked_at < cutoff)
            )
            if floor is not None:
                stmt = stmt.where(outbound_buffer.c.seq < floor)
            result = conn.execute(stmt)
        return result.rowcount


def _conforms_outbound_store(x: OutboundStore) -> IWriteOutboundRepository:
    return x
