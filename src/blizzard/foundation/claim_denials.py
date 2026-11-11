"""Why a claim was refused outright."""

from __future__ import annotations

from enum import StrEnum


class ClaimDenialReason(StrEnum):
    """The closed set of reasons an incompatible-claim ``409`` names: ``capability``, the runner's stored
    capabilities no longer run every statically reachable runner-owned lineage of the chunk; ``project``, the runner
    does not serve the chunk's project; ``binding``, the runner serves the project but not through the claimed
    workspace now — the binding is flagged, absent, or for another workspace, or the subscription is unrostered;
    ``targets``, the repository targets the runner prepared its environments at are not the chunk's — stale, or absent
    while a project link names its own branch."""

    CAPABILITY = "capability"
    PROJECT = "project"
    BINDING = "binding"
    TARGETS = "targets"
