"""Chunk deletion — the operator's withdrawal of an unacquired chunk's hub items, and
the chunk itself (issue #364). A hub item and its chunk live and die together: deleting
the chunk withdraws every open ``hub:``-source pointer it holds, in one composite store
write — reached from both a direct chunk delete and an unacquired holder's withdrawal.
Gated the same way grouping is: a paused or human-held chunk is refused too, not only a
runner-held one."""

from __future__ import annotations

import threading

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.queue import GROUPABLE_STATUSES, ChunkNotFound
from blizzard.hub.domain.work import Chunk, ChunkStatus, IReadChunkRepository, IWriteWorkItemRepository


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
    cascading delete of an unacquired holder."""

    def __init__(
        self,
        *,
        chunks: IReadChunkRepository,
        items: IWriteWorkItemRepository,
        clock: IClock,
        claim_lock: threading.Lock,
    ) -> None:
        self._chunks = chunks
        self._items = items
        self._clock = clock
        # Shared with ClaimService/EditService/RestartService (issue #120), so a claim
        # can't land on a chunk this write is mid-way through deleting.
        self._claim_lock = claim_lock

    def delete(self, chunk: Chunk, *, by: str) -> int:
        """Append ``chunk.deleted`` and withdraw every open ``hub:``-source item
        ``chunk`` holds, atomically. Raises :class:`ChunkNotFound` for a chunk already
        grouped or deleted away, :class:`ChunkNotDeletable` for one a runner or a human
        holds, or one terminal. Derives the guard's status fresh under the lock from a
        single ``load_facts`` call, exactly as ``EditService.edit`` does."""
        with self._claim_lock:
            facts = self._chunks.load_facts(chunk.chunk_id)
            if facts is None:
                raise ChunkNotFound(chunk.chunk_id)
            status = facts.status()
            if status not in GROUPABLE_STATUSES:
                raise ChunkNotDeletable(chunk.chunk_id, status)
            return self._items.delete_chunk_and_withdraw_hub_items(chunk, by=by, at=self._clock.now())
