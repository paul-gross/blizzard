"""Chunk deletion — the operator's withdrawal of an unacquired chunk's hub items, and
the chunk itself. A hub item and its chunk live and die together: deleting
the chunk withdraws every open ``hub:``-source pointer it holds, in one composite store
write.
Gated the same way grouping is: a paused or human-held chunk is refused too, not only a
runner-held one."""

from __future__ import annotations

# The residual dependency-graph lock — recorded debt, blizzard-context:/architecture/system-shape/exclusive-writes.md
# ast-grep-ignore: bzh:store-exclusive-write
import threading
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Protocol

from blizzard.foundation.chunk_status import PRE_CLAIM_STATUSES, ChunkDeleteRefusal, ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, ChunkVerb, DependencyEdge, WorkRef
from blizzard.hub.domain.chunk.ports.dependencies import IWriteChunkDependenciesRepository
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.record import IWriteChunkRecordRepository
from blizzard.hub.domain.kernel.hub_source import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.kernel.transactions import IHubTransactions, Transaction


class IHubItemWithdrawals(Protocol):
    def withdraw_open(self, pointer: WorkRef, *, at: datetime, transaction: Transaction | None = None) -> None: ...


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


def dependent_chunk_ids(chunk_id: str, standing: Iterable[DependencyEdge]) -> list[str]:
    """The chunks a standing edge in ``standing`` names ``chunk_id`` as prerequisite for, sorted."""
    return sorted(e.dependent_chunk_id for e in standing if e.prerequisite_chunk_id == chunk_id)


def delete_refusal(facts: ChunkFacts, dependents: Sequence[str]) -> ChunkDeleteRefusal | None:
    """Why a delete would be refused, ``None`` when it would not: outside :attr:`ChunkVerb.DELETE`'s
    window first, then standing ``dependents`` (:func:`dependent_chunk_ids`) — the guard order
    :func:`require_deletable` raises in."""
    if not facts.admits(ChunkVerb.DELETE):
        return ChunkDeleteRefusal.STATUS
    if dependents:
        return ChunkDeleteRefusal.DEPENDENTS
    return None


def require_deletable(chunk_id: str, facts: ChunkFacts, standing: Iterable[DependencyEdge]) -> None:
    """Refuse a delete :func:`delete_refusal` refuses — :class:`ChunkNotDeletable` for the status,
    :class:`ChunkHasDependents` naming the dependents sorted for a standing prerequisite."""
    dependents = dependent_chunk_ids(chunk_id, standing)
    refusal = delete_refusal(facts, dependents)
    if refusal is ChunkDeleteRefusal.STATUS:
        raise ChunkNotDeletable(chunk_id, facts.status())
    if refusal is ChunkDeleteRefusal.DEPENDENTS:
        raise ChunkHasDependents(chunk_id, dependents)


class DeleteService:
    """Delete an unacquired chunk, withdrawing the hub items it holds."""

    def __init__(
        self,
        *,
        items: IHubItemWithdrawals,
        clock: IClock,
        facts: IReadChunkFactsRepository,
        record: IWriteChunkRecordRepository,
        dependencies: IWriteChunkDependenciesRepository,
        cycle_lock: threading.Lock,
        transactions: IHubTransactions,
    ) -> None:
        self._items = items
        self._clock = clock
        self._facts = facts
        self._record = record
        self._dependencies = dependencies
        self._transactions = transactions
        # The residual per-tenant lock GroupService's own fold also shares: releasing this
        # chunk's own outgoing edges races a concurrent fold rewriting the same edges onto a
        # chunk neither one's row lock names — a row lock alone cannot close it.
        self._cycle_lock = cycle_lock

    def delete(self, chunk: Chunk, *, by: str) -> int:
        """Append ``chunk.deleted`` and withdraw every open ``hub:``-source item
        ``chunk`` holds, atomically. Raises :class:`ChunkNotFound` for one already
        grouped or deleted, :class:`ChunkNotDeletable` for one held or terminal, and
        :class:`ChunkHasDependents` for one a standing prerequisite for another chunk
        — every guard read taken fresh under the row lock."""
        with self._cycle_lock, self._transactions.begin() as transaction:
            locked = transaction.lock_chunks([chunk.chunk_id])
            facts = self._facts.load_facts(chunk.chunk_id, transaction=locked)
            if facts is None:
                raise ChunkNotFound(chunk.chunk_id)
            require_deletable(
                chunk.chunk_id, facts, self._dependencies.standing_edges_for(chunk.chunk_id, transaction=locked)
            )
            # Re-read fresh under the lock: a fold that landed a work ref onto this chunk
            # between the caller's own load and this lock must not have that ref survive
            # withdrawal because the write below still carries the caller's stale list.
            current = self._record.get(chunk.chunk_id, transaction=locked) or chunk
            at = self._clock.now()
            deleted_id = self._record.record_deleted(chunk.chunk_id, by=by, at=at, transaction=locked)
            self._dependencies.release_outgoing(chunk.chunk_id, by=by, at=at, transaction=locked)
            for pointer in current.work_refs:
                if pointer.source == RESERVED_HUB_SOURCE_NAME:
                    self._items.withdraw_open(pointer, at=at, transaction=locked)
            return deleted_id
