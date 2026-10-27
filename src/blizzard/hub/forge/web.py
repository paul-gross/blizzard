"""A forge's browser addresses — pure string work, shared by every binding that links into a forge's web pages
(``bzh:deterministic-shell``). The forge's effective web origin is the domain's to derive."""

from __future__ import annotations

from blizzard.hub.domain.config.forges import ConfiguredForge, derived_web_origin
from blizzard.hub.domain.config.repositories import RepositoryFields


def web_origin(api_base: str) -> str:
    """A GitHub forge's web origin from its API base (:func:`derived_web_origin`)."""
    return derived_web_origin("github", api_base)


def branch_url(forge: ConfiguredForge, repository: RepositoryFields, branch_name: str) -> str:
    """The browser ``tree`` address of ``branch_name`` on ``repository``, which lives on ``forge``."""
    return f"{forge.web_origin}/{repository.owner}/{repository.repo}/tree/{branch_name}"
