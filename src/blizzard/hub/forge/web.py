"""A GitHub-shaped forge's browser addresses, derived from its REST API base — pure string work,
shared by every binding that links into a forge's web pages (``bzh:deterministic-shell``)."""

from __future__ import annotations

from blizzard.hub.domain.config.repositories import RepositoryFields


def web_origin(api_base: str) -> str:
    """The forge's web origin from its API base — GitHub-adapter knowledge.

    Two unrelated derivations for one vendor — an ``api.`` host prefix for public GitHub,
    an ``/api/v3`` path suffix for Enterprise — so neither generalizes. Any other base is
    taken as its own web origin."""
    stripped = api_base.rstrip("/")
    if stripped.endswith("/api/v3"):
        return stripped[: -len("/api/v3")]
    if "://api." in stripped:
        return stripped.replace("://api.", "://", 1)
    return stripped


def branch_url(repository: RepositoryFields, branch_name: str) -> str:
    """The browser ``tree`` address of ``branch_name`` on ``repository``'s forge."""
    return f"{web_origin(repository.forge_api_url)}/{repository.owner}/{repository.repo}/tree/{branch_name}"
