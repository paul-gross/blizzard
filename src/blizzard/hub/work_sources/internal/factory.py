"""Builds the hub's work source registry from configuration.

One credentialed ``httpx.Client`` per configured ``[[work_source]]`` — never a shared
client, never a shared token: the delivery forge keeps its own client
(``hub/app.py``); this is the work-source seam's own composition. A ``provider -> builder`` map
selects the adapter; confined to ``internal/`` (``bzh:dependency-inversion``), so
``httpx`` construction for a work source stays out of the composition root.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from typing import cast

import httpx

from blizzard.hub.config import ConfigError, WorkSourceConfig
from blizzard.hub.work_sources.annotator import IWorkAnnotator
from blizzard.hub.work_sources.internal.github_work_source import GitHubWorkSource
from blizzard.hub.work_sources.registry import WorkSourceRegistry
from blizzard.hub.work_sources.source import IWorkSource

# The provider's default API origin, used when a source omits `api_base`.
_DEFAULT_API_BASES = {"github": "https://api.github.com"}


def _build_github(source: WorkSourceConfig, client: httpx.Client, api_base: str) -> IWorkSource:
    web_base = source.web_base or _derive_web_base(api_base)
    return GitHubWorkSource(client, name=source.name, repo=source.repo, web_base=web_base)


_BUILDERS: dict[str, Callable[[WorkSourceConfig, httpx.Client, str], IWorkSource]] = {"github": _build_github}


def _derive_web_base(api_base: str) -> str:
    """The provider's web origin from its API base — GitHub-adapter knowledge.

    Public GitHub splits ``api.github.com`` from ``github.com`` by stripping the
    ``api.`` host prefix; a GitHub Enterprise install splits
    ``git.corp.internal/api/v3`` from ``git.corp.internal`` by stripping the
    ``/api/v3`` path suffix — same vendor, two unrelated derivations, so neither can be
    inferred generically for a provider that follows neither rule."""
    stripped = api_base.rstrip("/")
    if stripped.endswith("/api/v3"):
        return stripped[: -len("/api/v3")]
    if "://api." in stripped:
        return stripped.replace("://api.", "://", 1)
    return stripped


def build_work_source_registry(sources: Sequence[WorkSourceConfig]) -> WorkSourceRegistry:
    """One credentialed client + binding per configured source.

    A source whose ``token_env`` names an unset variable fails here, at boot, naming
    the variable — not at first fetch. An empty ``sources`` is a legal, work-source-free
    hub. Only a source with ``annotate = true`` gets an entry in the registry's
    annotator map — every binding this factory builds today (the only provider,
    ``github``) implements :class:`~blizzard.hub.work_sources.annotator.IWorkAnnotator`
    on the same instance, so opting in is a matter of *exposing* the capability, not
    building a second object. This is what makes a non-opted source structurally
    never written to (``registry.annotator(name) is None``) rather than a runtime
    branch someone has to remember."""
    built: dict[str, IWorkSource] = {}
    annotators: dict[str, IWorkAnnotator] = {}
    for source in sources:
        builder = _BUILDERS.get(source.provider)
        if builder is None:
            raise ConfigError(f"work_source {source.name!r} has unknown provider {source.provider!r}")
        if source.token_env not in os.environ:
            raise ConfigError(f"work_source {source.name!r} names token_env {source.token_env!r}, which is unset")
        api_base = source.api_base or _DEFAULT_API_BASES[source.provider]
        client = httpx.Client(
            base_url=api_base,
            headers={"Authorization": f"token {os.environ[source.token_env]}"},
            timeout=30.0,
        )
        adapter = builder(source, client, api_base)
        built[source.name] = adapter
        if source.annotate:
            annotators[source.name] = cast(IWorkAnnotator, adapter)
    return WorkSourceRegistry(built, annotators)
