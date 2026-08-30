"""The graph-mint artifact-declaration repository seam (blizzard#410)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.artifacts import ArtifactKind

__all__ = ["GraphArtifactRecord", "IReadGraphArtifactRepository", "IWriteGraphArtifactRepository"]


@dataclass(frozen=True)
class GraphArtifactRecord:
    """One graph-scoped ``artifacts:`` declaration, pinned to the mint it was baked into
    — the runner's own mirror of the hub's ``graph_artifacts`` row, keyed
    ``(graph_id, name)``. ``ordinal`` is the authored ``artifacts:`` position, carried
    through as the envelope's own list order."""

    name: str
    ordinal: int
    kind: ArtifactKind
    content: str


class IReadGraphArtifactRepository(Protocol):
    """Read-only graph-artifact queries (held by read-path edges)."""

    def graph_artifacts_for_graph(self, graph_id: str) -> list[GraphArtifactRecord]:
        """This mint's pinned graph-scoped declarations, in authored order. Keyed on
        the mint's own ``graph_id``, never the lease — a lease pinned to a superseded mint
        keeps reading that mint's own rows. Empty for a mint that declared none, or one
        pinned before this runner ever recorded a pin."""
        ...


class IWriteGraphArtifactRepository(IReadGraphArtifactRepository, Protocol):
    """Read-write graph-artifact store — held only by the domain."""

    def record_graph_artifacts(
        self, *, graph_id: str, artifacts: list[GraphArtifactRecord], recorded_at: datetime
    ) -> None:
        """Pin a mint's graph-scoped declarations, insert-if-absent: a second call
        for the same ``graph_id`` — a second lease against the same mint — writes nothing
        new. Called by ``Spawner._mint`` before :meth:`record_lease`, so a crash between
        the two leaves only an orphan row a retry re-writes identically."""
        ...
