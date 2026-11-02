"""How a repository's changes land."""

from __future__ import annotations

from enum import StrEnum

from blizzard.foundation.component_name import ComponentNamed


class LandingPolicy(ComponentNamed, StrEnum):
    """A landing policy a write may name — how a repository's changes land."""

    __component_name__ = "LandingPolicyName"

    MERGE_ON_CHECKS = "merge-on-checks"
    FAST_FORWARD = "fast-forward"
    PARK = "park"
