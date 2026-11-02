"""Forge rate dialects, selected by kind: how each forge kind's answers become budget readings and refusal verdicts."""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.hub.forge.budget import IForgeRateDialect
from blizzard.hub.forge.internal.github_rates import GitHubRateDialect

#: Every forge kind's rate dialect; a unit guard holds one here for each known kind.
RATE_DIALECTS: Mapping[str, IForgeRateDialect] = {"github": GitHubRateDialect()}


def rate_dialect(kind: str) -> IForgeRateDialect:
    """The kind's rate dialect; an unknown kind fails at config load, so it cannot reach here."""
    return RATE_DIALECTS[kind]
