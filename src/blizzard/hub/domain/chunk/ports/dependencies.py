"""The chunk-dependencies repository seam — the declared dependent-on-prerequisite
edges between chunks."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple, Protocol

from blizzard.foundation.roles import dto
from blizzard.hub.domain.chunk.model import DependencyEdge
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead


@dto
class FoldMint(NamedTuple):
    """One edge a fold mints: the remapped pair, carrying the instant the edge it replaces
    was first declared — the fold's own instant stamps only the release."""

    dependent_chunk_id: str
    prerequisite_chunk_id: str
    declared_at: datetime


@dto
@dataclass(frozen=True)
class FoldTarget:
    """One folded chunk's own release/mint split for a single fold's dependency-edge
    rewrite."""

    chunk_id: str
    release: list[str]
    mint: list[FoldMint]


class IReadChunkDependenciesRepository(Protocol):
    """Read-only chunk-dependencies access. Answers four questions and no more: the
    fleet's standing edges, the standing edge for one ordered pair, one chunk's own
    standing edges in either role, and the standing edges of a set of dependents."""

    def list_standing_edges(self) -> list[DependencyEdge]:
        """Every currently-unreleased edge across the fleet."""
        ...

    def standing_edge(self, dependent_chunk_id: str, prerequisite_chunk_id: str) -> DependencyEdge | None:
        """The standing (unreleased) edge for this ordered pair, or ``None`` — at most
        one holds at a time, a domain-held invariant with no database constraint
        behind it (a released pair may carry other, released rows)."""
        ...

    def standing_edges_for_dependents(self, dependent_chunk_ids: Sequence[str]) -> list[DependencyEdge]:
        """`list_standing_edges`'s narrowed sibling (`bzh:bulk-reconstitution`) — every
        standing edge whose dependent is among ``dependent_chunk_ids``, bounded by those
        dependents rather than the fleet's edge history. Same order as
        :meth:`list_standing_edges`."""
        ...

    def standing_edges_for(self, chunk_id: str) -> list[DependencyEdge]:
        """Every standing edge naming ``chunk_id``, as dependent or as prerequisite alike,
        bounded by ``chunk_id``'s own edge count rather than the
        fleet's. Same order as :meth:`list_standing_edges`."""
        ...


class IWriteChunkDependenciesRepository(IReadChunkDependenciesRepository, Protocol):
    """Read-write chunk-dependencies access."""

    def declare_locked(
        self, handle: ILockedChunkRead, dependent_chunk_id: str, prerequisite_chunk_id: str, *, by: str, at: datetime
    ) -> DependencyEdge:
        """Mint a fresh standing edge for this ordered pair — always a new row, never a
        revive of a previously-released one — on ``handle``'s already-locked connection
        (``bzh:store-exclusive-write``). The caller has already checked that declaring is
        admitted and closes no cycle."""
        ...

    def release(
        self, dependent_chunk_id: str, prerequisite_chunk_id: str, *, by: str, at: datetime
    ) -> DependencyEdge | None:
        """Set ``released_at``/``released_by`` together, once, on the standing edge for
        this ordered pair. A no-op returning ``None`` when no edge stands."""
        ...

    def record_fold_locked(
        self, handle: ILockedChunkRead, targets: list[FoldTarget], *, grouped_into: str, by: str, at: datetime
    ) -> dict[str, int]:
        """Fold every target's dependency edges per its own release/mint split, atomically
        with recording each target's own ``chunk.grouped`` row, on ``handle``'s
        already-locked connection (``bzh:store-exclusive-write``). The split and the
        resulting set's cycle check are already done. Returns each target chunk id's
        freshly-inserted ``chunk_grouped.id``."""
        ...
