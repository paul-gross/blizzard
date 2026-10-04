"""Chunk deletion — the operator's withdrawal of an unacquired chunk's hub items, and
the chunk itself. A hub item and its chunk live and die together: deleting
the chunk withdraws every open ``hub:``-source pointer it holds, in one composite store
write — reached from both a direct chunk delete and an unacquired holder's withdrawal.
Gated the same way grouping is: a paused or human-held chunk is refused too, not only a
runner-held one."""

from __future__ import annotations

# The residual dependency-graph lock — recorded debt, blizzard-context:/architecture/system-shape/exclusive-writes.md
# ast-grep-ignore: bzh:store-exclusive-write
import threading

from blizzard.foundation.chunk_status import PRE_CLAIM_STATUSES, ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.model import Chunk, IWriteWorkItemRepository
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites


class ChunkNotDeletable(ValueError):
    """A delete targeted a chunk that is not free to be deleted."""

    def __init__(self, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(
            f"chunk {chunk_id} is {status.value} — deletion needs a chunk at "
            f"{' or '.join(sorted(s.value for s in PRE_CLAIM_STATUSES))}: "
            "no runner holding it, and no human hold or terminal on it either"
        )
        self.chunk_id = chunk_id
        self.status = status


class ChunkHasDependents(Exception):
    """A delete targeted a chunk that is a standing prerequisite for other chunks
    — refused, naming the dependents, rather than orphaning their edges."""

    def __init__(self, chunk_id: str, dependent_chunk_ids: list[str]) -> None:
        super().__init__(
            f"chunk {chunk_id} is a standing prerequisite for "
            f"{', '.join(dependent_chunk_ids)} and cannot be deleted while depended on"
        )
        self.chunk_id = chunk_id
        self.dependent_chunk_ids = dependent_chunk_ids


class DeleteService:
    """Delete an unacquired chunk, withdrawing the hub items it holds — the one pairing
    behind both a direct chunk delete and ``WorkItemEditService.withdraw``'s own
    cascading delete of an unacquired holder."""

    def __init__(
        self,
        *,
        items: IWriteWorkItemRepository,
        clock: IClock,
        exclusive: IChunkExclusiveWrites,
        cycle_lock: threading.Lock,
    ) -> None:
        self._items = items
        self._clock = clock
        # The locked-transaction seam (``bzh:store-exclusive-write``) shared with
        # ClaimService/EditService/RestartService, so a claim can't land on a chunk this
        # write is mid-way through deleting.
        self._exclusive = exclusive
        # The residual fleet-wide lock GroupService's own fold also shares: releasing this
        # chunk's own outgoing edges races a concurrent fold rewriting the same edges onto a
        # chunk neither one's row lock names — a row lock alone cannot close it.
        self._cycle_lock = cycle_lock

    def delete(self, chunk: Chunk, *, by: str) -> int:
        """Append ``chunk.deleted`` and withdraw every open ``hub:``-source item
        ``chunk`` holds, atomically. Raises :class:`ChunkNotFound` for one already
        grouped or deleted, :class:`ChunkNotDeletable` for one held or terminal, and
        :class:`ChunkHasDependents` for one a standing prerequisite for another chunk
        — every guard read taken fresh under the row lock."""
        with self._cycle_lock, self._exclusive.locked([chunk.chunk_id]) as handle:
            facts = handle.facts(chunk.chunk_id)
            if facts is None:
                raise ChunkNotFound(chunk.chunk_id)
            status = facts.status()
            if status not in PRE_CLAIM_STATUSES:
                raise ChunkNotDeletable(chunk.chunk_id, status)
            dependent_chunk_ids = sorted(
                edge.dependent_chunk_id
                for edge in handle.standing_edges()
                if edge.prerequisite_chunk_id == chunk.chunk_id
            )
            if dependent_chunk_ids:
                raise ChunkHasDependents(chunk.chunk_id, dependent_chunk_ids)
            # Re-read fresh under the lock: a fold that landed a work ref onto this chunk
            # between the caller's own load and this lock must not have that ref survive
            # withdrawal because the write below still carries the caller's stale list.
            current = handle.record(chunk.chunk_id) or chunk
            return self._items.delete_chunk_and_withdraw_hub_items_locked(handle, current, by=by, at=self._clock.now())
