"""Why a claim was refused outright — one vocabulary, shared by the hub that refuses and the runner that reads it."""

from __future__ import annotations

from enum import StrEnum


class ClaimDenialReason(StrEnum):
    """The closed set of reasons an incompatible-claim ``409`` names: ``capability``, the runner's stored
    capabilities no longer run every statically reachable runner-owned lineage of the chunk; ``project``, the runner
    does not serve the chunk's project.

    The runner reads this value from the claim response, so a third member is a breaking change to the runner-reached
    wire: a runner that predates it cannot tell what it was refused for."""

    CAPABILITY = "capability"
    PROJECT = "project"
