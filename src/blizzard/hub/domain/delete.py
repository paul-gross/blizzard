"""Chunk deletion — the operator's withdrawal of an unacquired chunk's hub items, and
the chunk itself (issue #364). A hub item and its chunk live and die together: deleting
the chunk withdraws every open ``hub:``-source pointer it holds, one composite store
write (D1/D4) — reached from both a direct chunk delete and an unacquired holder's
withdrawal (``WorkItemEditService.withdraw``, D3). Gated on
:data:`~blizzard.hub.domain.queue.GROUPABLE_STATUSES` exactly as grouping is: a paused
or human-held chunk is refused too, not only a runner-held one."""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.queue import GROUPABLE_STATUSES, ChunkNotFound
from blizzard.hub.domain.work import Chunk, ChunkFacts, ChunkStatus, IWriteChunkRepository, IWriteWorkItemRepository


class ChunkNotDeletable(ValueError):
    """A delete targeted a chunk that is not free to be deleted (issue #364)."""

    def __init__(self, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(
            f"chunk {chunk_id} is {status.value} — deletion needs a chunk at "
            f"{' or '.join(sorted(s.value for s in GROUPABLE_STATUSES))}: "
            "no runner holding it, and no human hold or terminal on it either"
        )
        self.chunk_id = chunk_id
        self.status = status


class DeleteService:
    """Delete an unacquired chunk, withdrawing the hub items it holds — the one pairing
    behind both a direct chunk delete and ``WorkItemEditService.withdraw``'s own
    cascading delete of an unacquired holder (D3)."""

    def __init__(self, *, chunks: IWriteChunkRepository, items: IWriteWorkItemRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._items = items
        self._clock = clock

    def delete(self, chunk: Chunk, *, by: str) -> int:
        """Append ``chunk.deleted`` and withdraw every open ``hub:``-source item
        ``chunk`` holds, atomically (D1/D4). Raises :class:`ChunkNotFound` for a chunk
        already grouped or deleted away, :class:`ChunkNotDeletable` for one a runner or
        a human holds, or one terminal. Returns the freshly-written ``chunk_deleted.id``."""
        self._require_deletable(chunk.chunk_id)
        return self._items.delete_chunk_and_withdraw_hub_items(chunk, by=by, at=self._clock.now())

    def _require_deletable(self, chunk_id: str) -> None:
        chunk = self._chunks.get(chunk_id)
        facts = self._chunks.load_facts(chunk_id)
        if chunk is None or facts is None:
            raise ChunkNotFound(chunk_id)
        status = (facts if facts is not None else ChunkFacts(minted=True)).status()
        if status not in GROUPABLE_STATUSES:
            raise ChunkNotDeletable(chunk_id, status)
