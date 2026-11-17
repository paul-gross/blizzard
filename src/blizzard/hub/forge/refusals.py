"""Forge refusal readers, selected by kind: how each forge kind's 401 and 403 answers become a classified refusal."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from blizzard.hub.domain.kernel.forge_refusals import ClassifiedRefusal
from blizzard.hub.forge.internal.github_refusals import GitHubRefusalReader


class IForgeRefusalReader(Protocol):
    """One forge kind's reading of a 401 or 403 answer the rate gate declined as a rate refusal."""

    def classify(
        self,
        status: int,
        headers: Mapping[str, str],
        message: str,
        *,
        documentation_url: str | None,
        operation: str,
        endpoint: str,
        repository: str | None,
    ) -> ClassifiedRefusal:
        """Why the forge refused the credential, with its sanitized diagnostic."""
        ...


#: Every forge kind's refusal reader; a unit guard holds one here for each known kind. Each answers this seam's
#: Protocol, which the work-source family's own reader Protocol matches structurally, so this seam never names the
#: family.
REFUSAL_READERS: Mapping[str, IForgeRefusalReader] = {"github": GitHubRefusalReader()}


def refusal_reader(kind: str) -> IForgeRefusalReader:
    """The kind's refusal reader; an unknown kind fails at config load, so it cannot reach here."""
    return REFUSAL_READERS[kind]
