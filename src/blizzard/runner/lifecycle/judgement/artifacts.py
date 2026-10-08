"""The graph-mint artifact-declaration repository seam, and the rules of a worker's read of
one artifact by name across node, graph, and system scope."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.artifacts import ArtifactKind, ArtifactScope
from blizzard.foundation.roles import domain_model

__all__ = [
    "ArtifactAmbiguous",
    "ArtifactCandidate",
    "ArtifactNotFound",
    "ArtifactRead",
    "ArtifactReadContradiction",
    "IReadGraphArtifactRepository",
    "IWriteGraphArtifactRepository",
    "PinnedGraphArtifact",
]


class ArtifactReadContradiction(Exception):
    """``--node`` paired with a scope that has no producing node — mapped to ``400``."""

    def __init__(self, node: str, scope: ArtifactScope) -> None:
        super().__init__(
            f"--node {node!r} cannot narrow {scope.value} scope — only node scope has a producing "
            f"node; drop one of --node / --scope {scope.value}"
        )


class ArtifactNotFound(Exception):
    """No artifact by that name in what the read searched — mapped to ``404``."""


class ArtifactAmbiguous(Exception):
    """More than one candidate for the name — mapped to ``409``, naming the candidates and
    only the narrowing flags still open to the caller."""

    def __init__(self, name: str, labels: Sequence[str], levers: Sequence[str]) -> None:
        hint = f" (pass {' and/or '.join(levers)} to disambiguate)" if levers else ""
        super().__init__(f"artifact {name!r} is ambiguous — found for: {', '.join(labels)}{hint}")
        self.labels = tuple(labels)
        self.levers = tuple(levers)


class ArtifactCandidate(Protocol):
    """What a read's resolution reads off one candidate artifact: its scope, and for a
    node-scoped one, its producing node."""

    @property
    def scope(self) -> ArtifactScope: ...
    @property
    def node_name(self) -> str | None: ...


def _label(candidate: ArtifactCandidate) -> str:
    if candidate.scope is ArtifactScope.NODE:
        return f"node {candidate.node_name}"
    if candidate.scope is ArtifactScope.SYSTEM:
        return "system"
    if candidate.scope is ArtifactScope.GRAPH:
        return "graph"
    raise AssertionError(f"unhandled artifact scope {candidate.scope!r}")  # pragma: no cover


@domain_model
@dataclass(frozen=True)
class ArtifactRead:
    """A worker's read of one artifact by ``name`` against its lease's mint ``graph_id``, optionally
    narrowed by ``node`` (a producing node) and ``scope``. A supplied ``node`` settles scope to node —
    neither a graph declaration nor a system artifact has a producing node — so graph and system join
    the search only when neither flag is given."""

    name: str
    graph_id: str
    node: str | None = None
    scope: ArtifactScope | None = None

    def validate(self) -> None:
        """Refuse ``node`` paired with a scope that has no producing node."""
        if self.node is not None and self.scope is not None and not self.scope.has_producing_node():
            raise ArtifactReadContradiction(self.node, self.scope)

    def searches_node(self) -> bool:
        return self.scope is None or self.scope is ArtifactScope.NODE

    def searches_graph(self) -> bool:
        return self.scope is ArtifactScope.GRAPH or (self.scope is None and self.node is None)

    def searches_system(self) -> bool:
        return self.scope is ArtifactScope.SYSTEM or (self.scope is None and self.node is None)

    def resolve[C: ArtifactCandidate](self, candidates: Sequence[C]) -> C:
        """The one candidate the searched scopes found, else :class:`ArtifactNotFound`
        naming what was searched, or :class:`ArtifactAmbiguous` for several."""
        if not candidates:
            raise ArtifactNotFound(self._not_found())
        if len(candidates) > 1:
            raise ArtifactAmbiguous(self.name, sorted({_label(c) for c in candidates}), self._levers(candidates))
        return candidates[0]

    def _not_found(self) -> str:
        if self.scope is ArtifactScope.GRAPH:
            return f"no graph-scoped artifact {self.name!r} pinned for this lease's mint {self.graph_id!r}"
        if self.scope is ArtifactScope.SYSTEM:
            return f"no system artifact {self.name!r}"
        # Names what was actually searched: a graph/system miss is only part of the story
        # when those scopes were in the search at all.
        qualifier = f" from node {self.node!r}" if self.node is not None else ""
        where = (
            f", nor pinned for its mint {self.graph_id!r}, nor a published system artifact"
            if self.searches_graph()
            else ""
        )
        return f"no artifact {self.name!r}{qualifier} for this node-step{where}"

    def _levers(self, candidates: Sequence[ArtifactCandidate]) -> tuple[str, ...]:
        """The narrowing flags that could actually change this result. ``--node`` names a
        *producing* node, which only a node-scoped candidate has, so it is offered only when
        the ambiguous set actually holds one — a graph/system-only collision has no producing
        node for ``--node`` to narrow, and advising it would send the caller toward an
        unrelated ``404`` instead of a resolution."""
        if self.node is not None:
            return ()
        node_scoped = any(c.scope is ArtifactScope.NODE for c in candidates)
        if self.scope is None:
            return ("--scope", "--node") if node_scoped else ("--scope",)
        return ("--node",) if node_scoped else ()


@domain_model
@dataclass(frozen=True)
class PinnedGraphArtifact:
    """One graph-scoped ``artifacts:`` declaration, pinned to the mint it was baked into
    — the runner's own mirror of the hub's ``graph_artifacts`` row, keyed
    ``(graph_id, name)``. ``ordinal`` is the authored ``artifacts:`` position, carried
    through as the envelope's own list order."""

    name: str
    ordinal: int
    kind: ArtifactKind
    content: str


class IReadGraphArtifactRepository(Protocol):
    """Read-only graph-artifact queries."""

    def graph_artifacts_for_graph(self, graph_id: str) -> list[PinnedGraphArtifact]:
        """This mint's pinned graph-scoped declarations, in authored order. Keyed on
        the mint's own ``graph_id``, never the lease — a lease pinned to a superseded mint
        keeps reading that mint's own rows. Empty for a mint that declared none, or one
        pinned before this runner ever recorded a pin."""
        ...


class IWriteGraphArtifactRepository(IReadGraphArtifactRepository, Protocol):
    """Read-write graph-artifact store — held only by the domain."""

    def record_graph_artifacts(
        self, *, graph_id: str, artifacts: list[PinnedGraphArtifact], recorded_at: datetime
    ) -> None:
        """Pin a mint's graph-scoped declarations, insert-if-absent: a second call
        for the same ``graph_id`` — a second lease against the same mint — writes nothing
        new."""
        ...
