"""Forge browser links, selected by kind without opening a credentialed client."""

from __future__ import annotations

from typing import Protocol

from blizzard.hub.domain.config.forges import ConfiguredForge, derived_web_origin
from blizzard.hub.domain.config.repositories import RepositoryFields
from blizzard.hub.forge.internal.github_web import GitHubWebLinks


def web_origin(api_base: str) -> str:
    """A GitHub forge's web origin from its API base (:func:`derived_web_origin`)."""
    return derived_web_origin("github", api_base)


class IForgeWebLinks(Protocol):
    """The browser addresses a forge binding can give for its repositories."""

    def branch_url(self, repository: RepositoryFields, branch_name: str) -> str: ...


def web_links(forge: ConfiguredForge) -> IForgeWebLinks | None:
    """The kind's pure link binding, absent for a forge kind without one."""
    if forge.kind == "github":
        return GitHubWebLinks(forge.web_origin)
    return None
