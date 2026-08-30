"""Artifact kind and scope, shared by both daemons (not the wheel-embedded static assets)."""

from __future__ import annotations

from enum import StrEnum


class ArtifactKind(StrEnum):
    """The union discriminator."""

    GIT_COMMIT = "git_commit"
    ASSET = "asset"


class ArtifactScope(StrEnum):
    """Where an artifact is pinned — a chunk's node-step, the graph mint (``artifacts:``),
    or blizzard's own published, global-namespace documents (``system``)."""

    NODE = "node"
    GRAPH = "graph"
    SYSTEM = "system"
