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

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, DependencyEdge, WorkRef
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.domain.runners.registration import RunnerRegistration
from blizzard.hub.domain.runners.route import Route


class ILockedChunkRead(Protocol):
    """Every guard read a locked writer consults, resolved on the one connection its row
    lock was taken on — so no guard read can race the write it precedes."""

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

    def live_statuses(self) -> dict[str, ChunkStatus]:
        """Derive the live roster on this transaction's connection."""
        ...


class ILockedFlowAdmissionRead(ILockedChunkRead, Protocol):
    """A chunk handle that also holds the config-authoring key before its chunk locks."""

    def flow_admission_locked(self) -> None: ...


class ILockedWorkRefRead(Protocol):
    """The guard read resolved on the one connection its pointer locks were taken on.
    Deliberately not an :class:`ILockedChunkRead`: a pointer-lock handle holds no chunk-row lock,
    so it cannot satisfy a chunk-row ``*_locked`` write."""

    def live_holders(self, pointers: Sequence[WorkRef]) -> dict[WorkRef, str]:
        """:meth:`~blizzard.hub.domain.chunk.ports.work_refs.IReadChunkWorkRefsRepository.live_holders`,
        read under the lock."""
        ...

    def source_links(self, sources: Sequence[str]) -> dict[str, list[ProjectId]]:
        """Each of ``sources`` mapped to the projects whose standing link draws from it, ordered by id —
        :meth:`~blizzard.hub.domain.projects.links.IReadProjectLinks.projects_linking_sources`, read under
        the lock — a source no project links maps to no projects."""
        ...


class IChunkExclusiveWrites(Protocol):
    """Opens the locked write transaction an exactly-one-wins decision and its excluded
    writers share."""

    def locked(self, chunk_ids: Sequence[str]) -> AbstractContextManager[ILockedChunkRead]:
        """Lock every named row, first, in sorted chunk-id order (closes cross-writer
        deadlock on Postgres — ``bzh:store-exclusive-write``), then yield the read handle
        bound to that one connection. A write against a locked chunk id goes through a
        write repository's own ``*_locked`` method, taking this same handle."""
        ...

    def locked_queue(self, chunk_ids: Sequence[str]) -> AbstractContextManager[ILockedChunkRead]:
        """Lock the queue key, then named and conservatively live chunks in sorted order.
        Ingest also takes the key before minting. Resolve decisions on this connection."""
        ...

    def locked_flow_admission(self, chunk_ids: Sequence[str]) -> AbstractContextManager[ILockedFlowAdmissionRead]:
        """Lock config-authoring, then the named chunks in sorted order."""
        ...

    def locked_dependency_graph(self, chunk_ids: Sequence[str]) -> AbstractContextManager[ILockedChunkRead]:
        """Lock the dependency graph key, then named chunks in sorted order."""
        ...

    def locked_claim(self, chunk_id: str) -> AbstractContextManager[ILockedChunkRead]:
        """Lock the graph, then the dependent and its standing prerequisites in sorted order."""
        ...

    def locked_work_refs(
        self, pointers: Sequence[WorkRef], *, project: ProjectId | None = None
    ) -> AbstractContextManager[ILockedWorkRefRead]:
        """Lock every named pointer, first, in sorted order, then yield the read handle bound
        to that one connection — for a decision whose race has no chunk row yet to lock. Each
        pointer locks only its own key. The queue key is taken first, before the graph key,
        so ingestion cannot add a backlog member during a queue decision. With
        ``project``, its source links' key is taken before any pointer's, the one order every
        locker of both keeps; a source link or unlink of that project waits for the commit. A
        write under the lock goes through a write repository's own ``*_locked`` method, taking
        this same handle."""
        ...
