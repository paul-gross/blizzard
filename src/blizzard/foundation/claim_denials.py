"""Why a claim was refused outright."""

from __future__ import annotations

from enum import StrEnum


class ClaimDenialReason(StrEnum):
    """The closed set of reasons an incompatible-claim ``409`` names: ``capability``, the runner can no longer run
    the chunk's reachable runner-owned lineages; ``project``, it does not serve the chunk's project; ``binding``, it
    serves the project but not through the claimed workspace now; ``targets``, its environments stand at repository
    targets that are not the chunk's."""

    CAPABILITY = "capability"
    PROJECT = "project"
    BINDING = "binding"
    TARGETS = "targets"
