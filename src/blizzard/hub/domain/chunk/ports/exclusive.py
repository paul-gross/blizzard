"""The locked-transaction seam (``bzh:store-exclusive-write``).

An exactly-one-wins decision — the chunk claim — and every writer it excludes open one
locked write transaction over the chunk ids their decision turns on, row-locked first,
guard reads and write both against the one connection that opens. This module declares
only the domain-facing Protocols; the connection itself never crosses here — a store
adapter implementing :class:`IChunkExclusiveWrites` holds it, and every other write
repository's own ``*_locked`` method receives the same handle back, never the
connection directly."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Protocol

from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, DependencyEdge
from blizzard.hub.domain.kernel.locks import LockKey
from blizzard.hub.domain.kernel.transactions import LockedTransaction
from blizzard.hub.domain.runners.registration import RunnerRegistration
from blizzard.hub.domain.runners.route import Route


class ILockedChunkRead(Protocol):
    """Every guard read a locked writer consults, resolved on the one connection its row
    lock was taken on — so no guard read can race the write it precedes."""

    @property
    def transaction(self) -> LockedTransaction:
        """The locked unit this handle reads in, which a repository method taking ``transaction`` joins
        (``bzh:transaction-participation``)."""
        ...

    def facts(self, chunk_id: str) -> ChunkFacts | None: ...
    def facts_for(self, chunk_ids: Sequence[str]) -> dict[str, ChunkFacts]:
        """`facts`'s batched sibling (`bzh:bulk-reconstitution`)."""
        ...

    def record(self, chunk_id: str) -> Chunk | None: ...
    def records_for(self, chunk_ids: Sequence[str]) -> dict[str, Chunk]:
        """`record`'s batched sibling (`bzh:bulk-reconstitution`) — the same wide
        row per id, with an unknown or ephemeral id dropped."""
        ...

    def route_of(self, chunk_id: str) -> Route | None: ...
    def is_ephemeral(self, chunk_id: str) -> bool: ...
    def standing_edges(self) -> list[DependencyEdge]:
        """Every currently-unreleased dependency edge across the fleet."""
        ...

    def standing_edges_touching(self, chunk_id: str) -> list[DependencyEdge]:
        """The unreleased edges naming ``chunk_id`` in either role — dependent or prerequisite."""
        ...

    def runner_registration(self, runner_id: str) -> RunnerRegistration | None: ...


class IChunkExclusiveWrites(Protocol):
    """Opens the locked write transaction an exactly-one-wins decision and its excluded
    writers share."""

    def locked(
        self, chunk_ids: Sequence[str], *, keys: Sequence[LockKey] = ()
    ) -> AbstractContextManager[ILockedChunkRead]:
        """Lock every named row, first, in sorted chunk-id order (closes cross-writer
        deadlock on Postgres — ``bzh:store-exclusive-write``), then ``keys`` in canonical order in the
        same lock set, then yield the read handle bound to that one connection. A write against a locked
        chunk id goes through a write repository's own ``*_locked`` method, taking this same handle, or a
        method taking its ``transaction``."""
        ...
