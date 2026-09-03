"""Chunk deletion — the operator's withdrawal of an unacquired chunk's hub items, and
the chunk itself (issue #364). A hub item and its chunk live and die together: deleting
the chunk withdraws every open ``hub:``-source pointer it holds, in one composite store
write — reached from both a direct chunk delete and an unacquired holder's withdrawal.
Gated the same way grouping is: a paused or human-held chunk is refused too, not only a
runner-held one."""

from __future__ import annotations

import threading

from blizzard.foundation.chunk_status import PRE_CLAIM_STATUSES, ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.dependencies import IReadChunkDependenciesRepository
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.errors import ChunkNotFound
from blizzard.hub.domain.work import Chunk, IWriteWorkItemRepository


class ChunkNotDeletable(ValueError):
    """A delete targeted a chunk that is not free to be deleted (issue #364)."""

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
    (issue #460) — refused, naming the dependents, rather than orphaning their edges."""

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
        facts: IReadChunkFactsRepository,
        items: IWriteWorkItemRepository,
        clock: IClock,
        claim_lock: threading.Lock,
        dependencies: IReadChunkDependenciesRepository,
    ) -> None:
        self._facts = facts
        self._items = items
        self._clock = clock
        # Shared with ClaimService/EditService/RestartService (issue #120), so a claim
        # can't land on a chunk this write is mid-way through deleting.
        self._claim_lock = claim_lock
        self._dependencies = dependencies

    def delete(self, chunk: Chunk, *, by: str) -> int:
        """Append ``chunk.deleted`` and withdraw every open ``hub:``-source item
        ``chunk`` holds, atomically. Raises :class:`ChunkNotFound` for one already
        grouped or deleted, :class:`ChunkNotDeletable` for one held or terminal, and
        :class:`ChunkHasDependents` for one a standing prerequisite for another chunk
        (issue #460) — every guard read taken fresh under the lock."""
        with self._claim_lock:
            facts = self._facts.load_facts(chunk.chunk_id)
            if facts is None:
                raise ChunkNotFound(chunk.chunk_id)
            status = facts.status()
            if status not in PRE_CLAIM_STATUSES:
                raise ChunkNotDeletable(chunk.chunk_id, status)
            dependent_chunk_ids = sorted(
                edge.dependent_chunk_id
                for edge in self._dependencies.list_standing_edges()
                if edge.prerequisite_chunk_id == chunk.chunk_id
            )
            if dependent_chunk_ids:
                raise ChunkHasDependents(chunk.chunk_id, dependent_chunk_ids)
            return self._items.delete_chunk_and_withdraw_hub_items(chunk, by=by, at=self._clock.now())
