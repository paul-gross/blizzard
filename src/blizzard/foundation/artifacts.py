"""Artifact kind and scope, shared by both daemons. ``ArtifactKind.ASSET`` is a chunk's
durable text/blob output — unrelated to :mod:`blizzard.foundation.assets`, the
wheel-embedded Angular static assets."""

from __future__ import annotations

from enum import StrEnum


class ArtifactKind(StrEnum):
    """The union discriminator."""

    GIT_COMMIT = "git_commit"
    ASSET = "asset"

    def carries_content(self) -> bool:
        """Whether an artifact of this kind carries text content: an asset does; a git
        commit carries only its ref (repo, branch, commit)."""
        return self is not ArtifactKind.GIT_COMMIT


class ArtifactScope(StrEnum):
    """Where an artifact is pinned — a chunk's node-step, the graph mint that baked it into
    the graph itself (``artifacts:``), or blizzard's own published, global-namespace
    documents, resolved at call time (``system``)."""

    NODE = "node"
    GRAPH = "graph"
    SYSTEM = "system"

    def has_producing_node(self) -> bool:
        """Whether an artifact in this scope names the node-step that produced it — only node
        scope does; a graph declaration and a system artifact have none."""
        return self is ArtifactScope.NODE

    @property
    def read_only_reason(self) -> str | None:
        """Why a worker cannot write into this scope, or ``None`` when it can: a worker writes
        only its own node-step's artifacts."""
        return _READ_ONLY_REASONS.get(self)


#: Why each read-only scope refuses a write — the domain fact, not just the word "read-only".
_READ_ONLY_REASONS: dict[ArtifactScope, str] = {
    ArtifactScope.GRAPH: "a graph's declarations are baked at mint",
    ArtifactScope.SYSTEM: "a system artifact is published by blizzard itself",
}
