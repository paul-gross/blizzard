"""The graph-mint artifact-declaration repository seam, and the rules of a worker's read of
artifacts: one by name across node, graph, and system scope, or one epoch of a node's series."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.artifacts import ArtifactKind, ArtifactScope
from blizzard.foundation.roles import domain_model
from blizzard.foundation.unrecognized import Unrecognized

__all__ = [
    "ArtifactAmbiguous",
    "ArtifactCandidate",
    "ArtifactEpochNotHeld",
    "ArtifactNotFound",
    "ArtifactRead",
    "ArtifactReadContradiction",
    "IReadGraphArtifactRepository",
    "IWriteGraphArtifactRepository",
    "PinnedGraphArtifact",
    "SeriesEntry",
    "carries_content",
    "has_producing_node",
    "read_only_reason",
    "refuse_epochs_outside_node",
    "series_newest_first",
]


def carries_content(kind: ArtifactKind) -> bool:
    """Whether an artifact of ``kind`` carries text content: an asset does; a git commit
    carries only its ref (repo, branch, commit)."""
    return kind is not ArtifactKind.GIT_COMMIT


def has_producing_node(scope: ArtifactScope) -> bool:
    """Whether an artifact in ``scope`` names the node-step that produced it — only node
    scope does; a graph declaration and a system artifact have none."""
    return scope is ArtifactScope.NODE


#: Why each read-only scope refuses a write — the domain fact, not just the word "read-only".
_READ_ONLY_REASONS: dict[ArtifactScope, str] = {
    ArtifactScope.GRAPH: "a graph's declarations are baked at mint",
    ArtifactScope.SYSTEM: "a system artifact is published by blizzard itself",
}


def read_only_reason(scope: ArtifactScope) -> str | None:
    """Why a worker cannot write into ``scope``, or ``None`` when it can: a worker writes
    only its own node-step's artifacts."""
    return _READ_ONLY_REASONS.get(scope)


class ArtifactReadContradiction(Exception):
    """A node-scope-only flag paired with a graph or system scope — mapped to ``400``. ``flag``
    is the offending flag, ``given`` the flag as the caller passed it, and ``because`` what only
    node scope has."""

    def __init__(self, flag: str, scope: ArtifactScope, *, given: str, verb: str, because: str) -> None:
        super().__init__(
            f"{given} cannot {verb} {scope.value} scope — only node scope {because}; "
            f"drop one of {flag} / --scope {scope.value}"
        )


def refuse_epochs_outside_node(scope: ArtifactScope | None) -> None:
    """Refuse a listing of superseded epochs narrowed to a scope that has none."""
    if scope is not None and not has_producing_node(scope):
        raise ArtifactReadContradiction(
            "--all-epochs", scope, given="--all-epochs", verb="include", because="has epochs"
        )


class ArtifactNotFound(Exception):
    """No artifact by that name in what the read searched — mapped to ``404``."""


class ArtifactEpochNotHeld(Exception):
    """The node-scope series exists but holds no such epoch — mapped to ``404``, naming the
    epochs it does hold, newest first."""

    def __init__(self, name: str, node: str | None, epoch: int, held: Sequence[int]) -> None:
        source = f" from node {node!r}" if node is not None else ""
        plural = "epoch" if len(held) == 1 else "epochs"
        super().__init__(
            f"artifact {name!r}{source} holds no epoch {epoch} — it holds {plural} {', '.join(map(str, held))}"
        )


class ArtifactAmbiguous(Exception):
    """The name resolves in more than one scope — mapped to ``409``, naming the surviving
    candidates and only the narrowing flag still open to the caller."""

    def __init__(self, name: str, labels: Sequence[str], levers: Sequence[str]) -> None:
        hint = f" (pass {' and/or '.join(levers)} to disambiguate)" if levers else ""
        super().__init__(f"artifact {name!r} is ambiguous — found for: {', '.join(labels)}{hint}")
        self.labels = tuple(labels)
        self.levers = tuple(levers)


class ArtifactCandidate(Protocol):
    """What a read's resolution reads off one candidate artifact: its scope, and for a
    node-scoped one, its producing node and the epoch it was produced under."""

    @property
    def scope(self) -> ArtifactScope | Unrecognized: ...
    @property
    def node_name(self) -> str | None: ...
    @property
    def epoch(self) -> int | None: ...


def _label(candidate: ArtifactCandidate) -> str:
    if candidate.scope is ArtifactScope.NODE:
        return f"node {candidate.node_name}"
    if candidate.scope is ArtifactScope.SYSTEM:
        return "system"
    if candidate.scope is ArtifactScope.GRAPH:
        return "graph"
    raise AssertionError(f"unhandled artifact scope {candidate.scope!r}")  # pragma: no cover


class SeriesEntry(Protocol):
    """What ordering a node series reads off one entry."""

    @property
    def node_name(self) -> str: ...
    @property
    def name(self) -> str: ...
    @property
    def epoch(self) -> int: ...
    @property
    def produced_at(self) -> str | None: ...


def series_newest_first[E: SeriesEntry](entries: Sequence[E]) -> list[E]:
    """One entry per ``(node_name, name, epoch)``, newest first by epoch then production time, an
    unstated time ranking oldest. ``entries`` arrive earliest write first within a key, and that
    first one is kept: the row a bare read serves."""
    kept: dict[tuple[str, str, int], E] = {}
    for entry in entries:
        kept.setdefault((entry.node_name, entry.name, entry.epoch), entry)
    return sorted(kept.values(), key=lambda e: (e.epoch, e.produced_at or ""), reverse=True)


@domain_model
@dataclass(frozen=True)
class ArtifactRead:
    """A worker's read of one artifact by ``name`` against its lease's mint ``graph_id``, optionally
    narrowed by ``node``, ``epoch`` and ``scope``. ``node`` or ``epoch`` settles scope to node, so
    graph and system join the search only when no narrowing flag is given."""

    name: str
    graph_id: str
    node: str | None = None
    scope: ArtifactScope | None = None
    epoch: int | None = None

    def validate(self) -> None:
        """Refuse ``node`` or ``epoch`` paired with a scope that has neither."""
        if self.scope is None or has_producing_node(self.scope):
            return
        if self.node is not None:
            raise ArtifactReadContradiction(
                "--node", self.scope, given=f"--node {self.node!r}", verb="narrow", because="has a producing node"
            )
        if self.epoch is not None:
            raise ArtifactReadContradiction(
                "--epoch", self.scope, given=f"--epoch {self.epoch}", verb="narrow", because="has epochs"
            )

    def searches_node(self) -> bool:
        return self.scope is None or self.scope is ArtifactScope.NODE

    def searches_graph(self) -> bool:
        return self.scope is ArtifactScope.GRAPH or self._searches_everywhere()

    def searches_system(self) -> bool:
        return self.scope is ArtifactScope.SYSTEM or self._searches_everywhere()

    def _searches_everywhere(self) -> bool:
        return self.scope is None and self.node is None and self.epoch is None

    def resolve[C: ArtifactCandidate](self, candidates: Sequence[C]) -> C:
        """The one candidate the searched scopes found, else :class:`ArtifactNotFound`, or
        :class:`ArtifactAmbiguous` for a name in several scopes. With ``epoch``, only the series'
        entries at it survive — :class:`ArtifactEpochNotHeld` when there are none. Node candidates
        collapse to the highest epoch, a tie keeping the first given."""
        if not candidates:
            raise ArtifactNotFound(self._not_found())
        if self.epoch is not None:
            candidates = self._at_epoch(self.epoch, candidates)
        survivors = self._collapse_node_series(candidates)
        if len(survivors) > 1:
            raise ArtifactAmbiguous(self.name, sorted({_label(c) for c in survivors}), self._levers())
        return survivors[0]

    def _at_epoch[C: ArtifactCandidate](self, epoch: int, series: Sequence[C]) -> list[C]:
        matches = [c for c in series if c.epoch == epoch]
        if not matches:
            held = sorted({c.epoch for c in series if c.epoch is not None}, reverse=True)
            raise ArtifactEpochNotHeld(self.name, self.node, epoch, held)
        return matches

    @staticmethod
    def _collapse_node_series[C: ArtifactCandidate](candidates: Sequence[C]) -> list[C]:
        nodes = [c for c in candidates if c.scope is ArtifactScope.NODE]
        others = [c for c in candidates if c.scope is not ArtifactScope.NODE]
        if len(nodes) > 1:
            nodes = [max(nodes, key=lambda c: -1 if c.epoch is None else c.epoch)]
        return nodes + others

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

    def _levers(self) -> tuple[str, ...]:
        """The narrowing flag that could change this result. Node candidates have already
        collapsed to one, so a refusal is a cross-scope collision and only ``--scope`` settles it;
        a supplied ``--node``, ``--epoch`` or ``--scope`` leaves at most one candidate, so no lever."""
        return ("--scope",) if self._searches_everywhere() else ()


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
