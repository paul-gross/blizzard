"""Builds the hub's PM source registry from configuration (D-108).

One credentialed ``httpx.Client`` per configured ``[[pm_source]]`` — never a shared
client, never a shared token (D-084/D-108): the delivery forge keeps its own client
(``hub/app.py``); this is the PM seam's own composition. A ``provider -> builder`` map
selects the adapter; confined to ``internal/`` (``bzh:dependency-inversion``), so
``httpx`` construction for PM stays out of the composition root.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence

import httpx

from blizzard.hub.config import ConfigError, PmSourceConfig
from blizzard.hub.pm.internal.github_pm_source import GitHubPmSource
from blizzard.hub.pm.registry import PmSourceRegistry
from blizzard.hub.pm.source import IPmSource

# The provider's default API origin, used when a source omits `api_base` (D-108).
_DEFAULT_API_BASES = {"github": "https://api.github.com"}


def _build_github(source: PmSourceConfig, client: httpx.Client, api_base: str) -> IPmSource:
    web_base = source.web_base or _derive_web_base(api_base)
    return GitHubPmSource(client, name=source.name, repo=source.repo, web_base=web_base)


_BUILDERS: dict[str, Callable[[PmSourceConfig, httpx.Client, str], IPmSource]] = {"github": _build_github}


def _derive_web_base(api_base: str) -> str:
    """The provider's web origin from its API base (D-108) — GitHub-adapter knowledge.

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


def build_pm_registry(sources: Sequence[PmSourceConfig]) -> PmSourceRegistry:
    """One credentialed client + binding per configured source.

    A source whose ``token_env`` names an unset variable fails here, at boot, naming
    the variable — not at first fetch. An empty ``sources`` is a legal, PM-reach-free
    hub (D-108)."""
    built: dict[str, IPmSource] = {}
    for source in sources:
        builder = _BUILDERS.get(source.provider)
        if builder is None:
            raise ConfigError(f"pm_source {source.name!r} has unknown provider {source.provider!r}")
        if source.token_env not in os.environ:
            raise ConfigError(f"pm_source {source.name!r} names token_env {source.token_env!r}, which is unset")
        api_base = source.api_base or _DEFAULT_API_BASES[source.provider]
        client = httpx.Client(
            base_url=api_base,
            headers={"Authorization": f"token {os.environ[source.token_env]}"},
            timeout=30.0,
        )
        built[source.name] = builder(source, client, api_base)
    return PmSourceRegistry(built)
