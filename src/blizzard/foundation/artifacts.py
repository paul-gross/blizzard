"""The artifact vocabulary both daemons speak — kind and scope.

Not to be confused with :mod:`blizzard.foundation.assets` (wheel-embedded Angular
static assets); this module's artifacts are a chunk's durable outputs."""

from __future__ import annotations

from enum import StrEnum


class ArtifactKind(StrEnum):
    """The union discriminator."""

    GIT_COMMIT = "git_commit"
    ASSET = "asset"


class ArtifactScope(StrEnum):
    """Where an artifact is pinned — a chunk's node-step, the graph mint that baked it
    into the graph itself (``artifacts:``), or blizzard's own published, global-namespace
    documents, resolved at call time (``system``)."""

    NODE = "node"
    GRAPH = "graph"
    SYSTEM = "system"
