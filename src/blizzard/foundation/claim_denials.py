"""Why a claim was refused outright."""

from __future__ import annotations

from enum import StrEnum


class ClaimDenialReason(StrEnum):
    """The closed set of reasons an incompatible-claim ``409`` names: ``capability``, the runner's stored
    capabilities no longer run every statically reachable runner-owned lineage of the chunk; ``project``, the runner
    does not serve the chunk's project."""

    CAPABILITY = "capability"
    PROJECT = "project"
