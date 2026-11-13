"""Forge refusal readers, selected by kind: how each forge kind's 401 and 403 answers become a classified refusal."""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.hub.forge.internal.github_refusals import GitHubRefusalReader

#: Every forge kind's refusal reader; a unit guard holds one here for each known kind. Each answers the work-source
#: family's refusal-reader Protocol structurally, so this seam never names the family.
REFUSAL_READERS: Mapping[str, GitHubRefusalReader] = {"github": GitHubRefusalReader()}


def refusal_reader(kind: str) -> GitHubRefusalReader:
    """The kind's refusal reader; an unknown kind fails at config load, so it cannot reach here."""
    return REFUSAL_READERS[kind]
