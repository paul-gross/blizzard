"""Artifact kind and scope, shared by both daemons. ``ArtifactKind.ASSET`` is a chunk's
durable text/blob output — unrelated to :mod:`blizzard.foundation.assets`, the
wheel-embedded Angular static assets."""

from __future__ import annotations

from enum import StrEnum


class ArtifactKind(StrEnum):
    """The union discriminator."""

    GIT_COMMIT = "git_commit"
    ASSET = "asset"


class ArtifactScope(StrEnum):
    """Where an artifact is pinned — a chunk's node-step, the graph mint that baked it into
    the graph itself (``artifacts:``), or blizzard's own published, global-namespace
    documents, resolved at call time (``system``)."""

    NODE = "node"
    GRAPH = "graph"
    SYSTEM = "system"
