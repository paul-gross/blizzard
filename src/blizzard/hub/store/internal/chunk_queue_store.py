"""SQLAlchemy adapter for the chunk queue seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Connection

from blizzard.foundation.clock import IClock
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.chunk.ports.queue import (
    BACKLOG_RANKING_LOCK,
    READY_RANKING_LOCK,
    IWriteChunkQueueRepository,
)
from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_rows import (
    after_newest,
    currently_promoted_ats,
    insert_demote_rows,
    insert_promote_rows,
)
from blizzard.hub.store.internal.newest_fact import newest_fact_select
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.store.transactions import connection_for, connection_in


class ChunkQueueStore:
    """The chunk's promotion and queue/backlog-position facts."""

    def __init__(self, store: ScopedConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def queue_positions(self, chunk_ids: Sequence[str], *, transaction: Transaction | None = None) -> dict[str, float]:
        """The newest explicit queue position per chunk among ``chunk_ids`` — the ordering
        the peek honours."""
        result: dict[str, float] = {}
        with connection_in(self._store, transaction, "queue_positions") as conn:
            for batch in id_batches(chunk_ids):
                rows = conn.execute(
                    newest_fact_select(
                        self._store,
                        s.queue_positions,
                        s.queue_positions.c.chunk_id,
                        batch,
                        s.queue_positions.c.chunk_id,
                        s.queue_positions.c.position,
                    )
                ).all()
                result.update((r.chunk_id, float(r.position)) for r in rows)
        return result

    def promoted_ats(self, chunk_ids: Sequence[str], *, transaction: Transaction | None = None) -> dict[str, datetime]:
        """Each currently promoted chunk's newest ``chunk_promoted.promoted_at`` among ``chunk_ids``."""
        result: dict[str, datetime] = {}
        with connection_in(self._store, transaction, "promoted_ats") as conn:
            for batch in id_batches(chunk_ids):
                result.update(currently_promoted_ats(conn, batch))
        return result

    def record_promote(self, chunk_id: str, *, at: datetime) -> int | None:
        with self._store.write("record_promote") as conn:
            if currently_promoted_ats(conn, [chunk_id]):
                return None
            at = after_newest(conn, s.chunk_demoted.c.demoted_at, chunk_id, at)
            result = conn.execute(self._store.insert(s.chunk_promoted).values(chunk_id=chunk_id, promoted_at=at))
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else None

    def record_promote_with_position(
        self, chunk_id: str, *, position: float, at: datetime, transaction: LockedTransaction
    ) -> int:
        # One transaction: a crash between the two writes would otherwise let a stale
        # backlog position outrank the stamp on restart.
        return insert_promote_rows(
            self._ranked_chunk_connection(transaction, chunk_id, READY_RANKING_LOCK),
            chunk_id,
            position=position,
            at=at,
        )

    def record_demote_with_position(
        self, chunk_id: str, *, position: float, at: datetime, transaction: LockedTransaction
    ) -> int:
        # One transaction: a crash between the two writes would otherwise rank the demoted chunk
        # by its stale ready-queue position, which the shared newest-wins positions would keep.
        return insert_demote_rows(
            self._ranked_chunk_connection(transaction, chunk_id, BACKLOG_RANKING_LOCK),
            chunk_id,
            position=position,
            at=at,
        )

    def record_queue_positions(
        self, positions: Sequence[tuple[str, float]], *, at: datetime, transaction: LockedTransaction
    ) -> None:
        """`src/blizzard/hub/domain/chunk/ports/queue.py`'s `IWriteChunkQueueRepository.record_queue_positions`.

        One multi-row insert carries every pair."""
        conn = connection_for(self._store, transaction, key=READY_RANKING_LOCK)
        if not positions:
            return
        conn.execute(
            self._store.insert(s.queue_positions),
            [{"chunk_id": chunk_id, "position": position, "set_at": at} for chunk_id, position in positions],
        )

    def record_backlog_positions(
        self, positions: Sequence[tuple[str, float]], *, at: datetime, transaction: LockedTransaction
    ) -> None:
        conn = connection_for(self._store, transaction, key=BACKLOG_RANKING_LOCK)
        if not positions:
            return
        chunk_ids = [chunk_id for chunk_id, _ in positions]
        # `.in_()` reads over id_batches, not one read per pair.
        promoted: set[str] = set()
        for batch in id_batches(chunk_ids):
            promoted |= currently_promoted_ats(conn, batch).keys()
        # A legacy store-side predicate, kept as recorded debt (``bzh:store-refuses-stale``): a pair
        # naming a currently promoted chunk is dropped, a chunk demoted since its promotion is not.
        rows = [
            {"chunk_id": chunk_id, "position": position, "set_at": at}
            for chunk_id, position in positions
            if chunk_id not in promoted
        ]
        if rows:
            conn.execute(self._store.insert(s.queue_positions), rows)

    def _ranked_chunk_connection(
        self, transaction: LockedTransaction, chunk_id: str, ranking_lock: tuple[str, str]
    ) -> Connection:
        """``transaction``'s connection, refused unless it locks both ``chunk_id`` and ``ranking_lock``."""
        connection_for(self._store, transaction, key=ranking_lock)
        return connection_for(self._store, transaction, chunk=chunk_id)


_queue_conforms: type[IWriteChunkQueueRepository] = ChunkQueueStore
