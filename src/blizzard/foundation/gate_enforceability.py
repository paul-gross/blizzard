"""Why a runner's configured gate can never be enforced — one definition, shared by the hub and its wire."""

from __future__ import annotations

from enum import StrEnum


class UnenforceableGateReason(StrEnum):
    #: Every non-retired graph with a node of this name runs it on the hub, beyond a runner gate set's reach.
    HUB_EXECUTED = "hub-executed"
    #: No non-retired graph has a node of this name.
    UNKNOWN_NODE = "unknown-node"
