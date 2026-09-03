"""The chunk-dependencies repository seam — the declared dependent-on-prerequisite
edges between chunks (issue #456)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.work import DependencyEdge


class IReadChunkDependenciesRepository(Protocol):
    """Read-only chunk-dependencies access. Answers two questions and no more: the
    fleet's standing edges, and the standing edge for one ordered pair."""

    def list_standing_edges(self) -> list[DependencyEdge]:
        """Every currently-unreleased edge across the fleet."""
        ...

    def standing_edge(self, dependent_chunk_id: str, prerequisite_chunk_id: str) -> DependencyEdge | None:
        """The standing (unreleased) edge for this ordered pair, or ``None`` — at most
        one holds at a time, a domain-held invariant with no database constraint
        behind it (a released pair may carry other, released rows)."""
        ...


class IWriteChunkDependenciesRepository(IReadChunkDependenciesRepository, Protocol):
    """Read-write chunk-dependencies access."""

    def declare(self, dependent_chunk_id: str, prerequisite_chunk_id: str, *, by: str, at: datetime) -> DependencyEdge:
        """Mint a fresh standing edge for this ordered pair — always a new row, never a
        revive of a previously-released one. The caller has already checked, under the
        claim lock, that declaring is admitted and closes no cycle."""
        ...

    def release(
        self, dependent_chunk_id: str, prerequisite_chunk_id: str, *, by: str, at: datetime
    ) -> DependencyEdge | None:
        """Set ``released_at``/``released_by`` together, once, on the standing edge for
        this ordered pair. A no-op returning ``None`` when no edge stands."""
        ...

    def record_fold(
        self,
        chunk_id: str,
        *,
        grouped_into: str,
        release: list[str],
        mint: list[tuple[str, str]],
        by: str,
        at: datetime,
    ) -> int:
        """Fold ``chunk_id``'s dependency edges per the caller's own release/mint decision
        (D1, D3, D4, issue #460), atomically with recording its ``chunk.grouped`` row. The
        caller (:class:`~blizzard.hub.domain.queue.GroupService`) has already computed the
        split and checked the resulting set closes no cycle."""
        ...
