"""SQLAlchemy adapter for the chunk queue seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.queue import IWriteChunkQueueRepository
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_rows import insert_promote_rows, row_exists


class ChunkQueueStore:
    """The chunk's promotion and queue/backlog-position facts."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def queue_positions(self) -> dict[str, float]:
        """The newest explicit queue position per chunk — the ordering the peek honours."""
        with self._store.read("queue_positions") as conn:
            rows = conn.execute(
                select(s.queue_positions.c.chunk_id, s.queue_positions.c.position, s.queue_positions.c.id).order_by(
                    s.queue_positions.c.id
                )
            ).all()
        # id is monotonic per insert, so the last row seen for a chunk is its newest fact.
        return {r.chunk_id: float(r.position) for r in rows}

    def promoted_ats(self) -> dict[str, datetime]:
        """Each promoted chunk's ``chunk_promoted.promoted_at`` (issue #137)."""
        with self._store.read("promoted_ats") as conn:
            rows = conn.execute(select(s.chunk_promoted.c.chunk_id, s.chunk_promoted.c.promoted_at)).all()
        return {r.chunk_id: r.promoted_at for r in rows}

    def record_promote(self, chunk_id: str, *, at: datetime) -> int | None:
        # Idempotent by chunk_id: a chunk already promoted keeps its first row, so a
        # double promote (board click, CLI retry) is a harmless no-op.
        with self._store.write("record_promote") as conn:
            if row_exists(conn, s.chunk_promoted, chunk_id):
                return None
            result = conn.execute(s.chunk_promoted.insert().values(chunk_id=chunk_id, promoted_at=at))
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else None

    def record_promote_with_tail_position(self, chunk_id: str, *, position: float, at: datetime) -> int | None:
        # One transaction: a crash between the two writes would otherwise let a stale
        # backlog position outrank the tail stamp on restart.
        with self._store.write("record_promote_with_tail_position") as conn:
            if row_exists(conn, s.chunk_promoted, chunk_id):
                return None
            return insert_promote_rows(conn, chunk_id, position=position, at=at)

    def record_queue_positions(self, positions: Sequence[tuple[str, float]], *, at: datetime) -> None:
        """Append every ``(chunk_id, position)`` pair in one multi-row insert — a
        whole-order replace of N chunks costs one write transaction, not N."""
        if not positions:
            return
        with self._store.write("record_queue_positions") as conn:
            conn.execute(
                s.queue_positions.insert(),
                [{"chunk_id": chunk_id, "position": position, "set_at": at} for chunk_id, position in positions],
            )

    def record_backlog_positions(self, positions: Sequence[tuple[str, float]], *, at: datetime) -> None:
        if not positions:
            return
        with self._store.write("record_backlog_positions") as conn:
            chunk_ids = [chunk_id for chunk_id, _ in positions]
            # One `.in_()` read for the whole batch, not one `row_exists` per pair.
            promoted = {
                r.chunk_id
                for r in conn.execute(
                    select(s.chunk_promoted.c.chunk_id).where(s.chunk_promoted.c.chunk_id.in_(chunk_ids))
                ).all()
            }
            rows = [
                {"chunk_id": chunk_id, "position": position, "set_at": at}
                for chunk_id, position in positions
                # promoted since the caller resolved backlog candidates — not this write's chunk anymore
                if chunk_id not in promoted
            ]
            if rows:
                conn.execute(s.queue_positions.insert(), rows)


def _conforms_queue(x: ChunkQueueStore) -> IWriteChunkQueueRepository:
    return x
