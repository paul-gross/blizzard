"""The chunk-dependencies repository seam — the declared dependent-on-prerequisite
edges between chunks (issue #456)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.work import DependencyEdge


@dataclass(frozen=True)
class FoldTarget:
    """One folded chunk's own release/mint split for a single fold's dependency-edge
    rewrite (D1, D3, issue #460)."""

    chunk_id: str
    release: list[str]
    mint: list[tuple[str, str]]


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
        targets: list[FoldTarget],
        *,
        grouped_into: str,
        by: str,
        at: datetime,
    ) -> dict[str, int]:
        """Fold every target's dependency edges per its own release/mint split (D1, D3,
        issue #460), atomically with recording each target's own ``chunk.grouped`` row —
        one transaction across the whole fold, so no target's write can commit ahead of
        another's (D4). The split and the resulting set's cycle check are already done.
        Returns each target chunk id's freshly-inserted ``chunk_grouped.id``."""
        ...
