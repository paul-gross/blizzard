"""Builds the hub's work source registry from configuration.

One credentialed ``httpx.Client`` per configured ``[[work_source]]`` — never a shared
client, never a shared token. A ``provider -> builder`` map selects the adapter; confined
to ``internal/`` (``bzh:dependency-inversion``), keeping ``httpx`` out of the root.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from typing import cast

import httpx

from blizzard.hub.config import ConfigError, WorkSourceConfig
from blizzard.hub.work_sources.annotator import IWorkAnnotator
from blizzard.hub.work_sources.closer import IWorkCloser
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

    Two unrelated derivations for one vendor — an ``api.`` host prefix for public GitHub,
    an ``/api/v3`` path suffix for Enterprise — so neither generalizes."""
    stripped = api_base.rstrip("/")
    if stripped.endswith("/api/v3"):
        return stripped[: -len("/api/v3")]
    if "://api." in stripped:
        return stripped.replace("://api.", "://", 1)
    return stripped


def build_work_source_registry(sources: Sequence[WorkSourceConfig]) -> WorkSourceRegistry:
    """One credentialed client + binding per configured source.

    A source whose ``token_env`` names an unset variable fails here, at boot, not at first
    fetch. Only an opted-in source gets an annotator/closer entry, so a non-opted one is
    structurally never written to rather than guarded by a runtime branch."""
    built: dict[str, IWorkSource] = {}
    annotators: dict[str, IWorkAnnotator] = {}
    closers: dict[str, IWorkCloser] = {}
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
        if source.close:
            closers[source.name] = cast(IWorkCloser, adapter)
    return WorkSourceRegistry(built, annotators, closers)
