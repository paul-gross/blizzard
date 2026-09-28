"""The chunk-dependencies repository seam — the declared dependent-on-prerequisite
edges between chunks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.chunks.exclusive import ILockedChunkRead
from blizzard.hub.domain.work import DependencyEdge


@dataclass(frozen=True)
class FoldTarget:
    """One folded chunk's own release/mint split for a single fold's dependency-edge
    rewrite."""

    chunk_id: str
    release: list[str]
    mint: list[tuple[str, str]]


class IReadChunkDependenciesRepository(Protocol):
    """Read-only chunk-dependencies access. Answers three questions and no more: the
    fleet's standing edges, the standing edge for one ordered pair, and one chunk's own
    standing edges in either role."""

    def list_standing_edges(self) -> list[DependencyEdge]:
        """Every currently-unreleased edge across the fleet."""
        ...

    def standing_edge(self, dependent_chunk_id: str, prerequisite_chunk_id: str) -> DependencyEdge | None:
        """The standing (unreleased) edge for this ordered pair, or ``None`` — at most
        one holds at a time, a domain-held invariant with no database constraint
        behind it (a released pair may carry other, released rows)."""
        ...

    def standing_edges_for(self, chunk_id: str) -> list[DependencyEdge]:
        """Every standing edge naming ``chunk_id``, as dependent or as prerequisite alike,
        bounded by ``chunk_id``'s own edge count rather than the
        fleet's. Same order as :meth:`list_standing_edges`."""
        ...


class IWriteChunkDependenciesRepository(IReadChunkDependenciesRepository, Protocol):
    """Read-write chunk-dependencies access."""

    def declare(self, dependent_chunk_id: str, prerequisite_chunk_id: str, *, by: str, at: datetime) -> DependencyEdge:
        """Mint a fresh standing edge for this ordered pair — always a new row, never a
        revive of a previously-released one. The caller has already checked, under the
        claim lock, that declaring is admitted and closes no cycle."""
        ...

    def declare_locked(
        self, handle: ILockedChunkRead, dependent_chunk_id: str, prerequisite_chunk_id: str, *, by: str, at: datetime
    ) -> DependencyEdge:
        """`declare`'s locked-transaction sibling (``bzh:store-exclusive-write``) — the
        dependency declare's own write, on ``handle``'s already-locked connection."""
        ...

    def release(
        self, dependent_chunk_id: str, prerequisite_chunk_id: str, *, by: str, at: datetime
    ) -> DependencyEdge | None:
        """Set ``released_at``/``released_by`` together, once, on the standing edge for
        this ordered pair. A no-op returning ``None`` when no edge stands."""
        ...

    def record_fold(
        self,
        targets: list[FoldTarget],
        *,
        grouped_into: str,
        by: str,
        at: datetime,
    ) -> dict[str, int]:
        """Fold every target's dependency edges per its own release/mint split,
        atomically with recording each target's own ``chunk.grouped`` row —
        one transaction across the whole fold, so no target's write can commit ahead of
        another's. The split and the resulting set's cycle check are already done.
        Returns each target chunk id's freshly-inserted ``chunk_grouped.id``."""
        ...

    def record_fold_locked(
        self, handle: ILockedChunkRead, targets: list[FoldTarget], *, grouped_into: str, by: str, at: datetime
    ) -> dict[str, int]:
        """`record_fold`'s locked-transaction sibling (``bzh:store-exclusive-write``) —
        the group fold's own write, on ``handle``'s already-locked connection."""
        ...
