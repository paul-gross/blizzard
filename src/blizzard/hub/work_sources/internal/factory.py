"""Builds the hub's work source registry from configuration.

One credentialed ``httpx.Client`` per configured ``[[work_source]]`` — never a shared
client, never a shared token. A ``provider -> entry`` map selects the adapter; confined
to ``internal/`` (``bzh:dependency-inversion``), keeping ``httpx`` out of the root.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

import httpx

from blizzard.foundation.clock import IClock
from blizzard.hub.auth.users import IReadUserRepository
from blizzard.hub.config import ConfigError, WorkSourceConfig
from blizzard.hub.domain.delete import DeleteService
from blizzard.hub.domain.work import IWriteWorkItemRepository
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.work_sources.annotator import IWorkAnnotator
from blizzard.hub.work_sources.closer import IWorkCloser
from blizzard.hub.work_sources.editor import IWorkEditor
from blizzard.hub.work_sources.internal.github_work_source import GitHubWorkSource
from blizzard.hub.work_sources.internal.hub_work_source import seat_hub_work_source
from blizzard.hub.work_sources.registry import WorkSourceRegistry
from blizzard.hub.work_sources.source import IWorkSource


@dataclass(frozen=True)
class WorkSourceEntry:
    """One ``[[work_source]]`` entry, resolved to the adapter it names."""

    config: WorkSourceConfig

    @classmethod
    def of(cls, config: WorkSourceConfig) -> WorkSourceEntry:
        kinds: dict[str, type[WorkSourceEntry]] = {"github": GithubEntry}
        kind = kinds.get(config.provider)
        if kind is None:
            raise ConfigError(f"work_source {config.name!r} has unknown provider {config.provider!r}")
        return kind(config)

    @classmethod
    def registry(
        cls,
        sources: Sequence[WorkSourceConfig],
        store: HubStoreConnections,
        clock: IClock,
        *,
        users: IReadUserRepository,
        work_item_store: IWriteWorkItemRepository,
        delete: DeleteService,
        close_forge_writes_enabled: bool = True,
    ) -> WorkSourceRegistry:
        """One credentialed client + binding per configured source, plus the built-in
        ``hub`` source (issue #357) — always seated, both an editor and a closer, neither
        opt-in. ``close_forge_writes_enabled=False`` seats no closer for a *configured*
        source (never the unaffected `hub` one) — see ``docs/deployment/work-sources.md``.
        A source's ``token_env`` naming an unset variable fails here, at boot."""
        built: dict[str, IWorkSource] = {}
        annotators: dict[str, IWorkAnnotator] = {}
        closers: dict[str, IWorkCloser] = {}
        editors: dict[str, IWorkEditor] = {}
        for config in sources:
            adapter = cls.of(config).source()
            built[config.name] = adapter
            if config.annotate:
                annotators[config.name] = cast(IWorkAnnotator, adapter)
            if close_forge_writes_enabled:
                closers[config.name] = cast(IWorkCloser, adapter)
        seat_hub_work_source(
            built, editors, closers, store=store, clock=clock, users=users, items=work_item_store, delete=delete
        )
        return WorkSourceRegistry(built, annotators, closers, editors)

    @property
    def token(self) -> str:
        env = self.config.token_env
        if env not in os.environ:
            raise ConfigError(f"work_source {self.config.name!r} names token_env {env!r}, which is unset")
        return os.environ[env]

    @property
    def client(self) -> httpx.Client:
        headers = {"Authorization": f"token {self.token}"}
        return httpx.Client(base_url=self.api_base, headers=headers, timeout=30.0)

    @property
    def api_base(self) -> str:
        raise NotImplementedError

    def source(self) -> IWorkSource:
        raise NotImplementedError


class GithubEntry(WorkSourceEntry):
    DEFAULT_API_BASE = "https://api.github.com"

    @property
    def api_base(self) -> str:
        return self.config.api_base or self.DEFAULT_API_BASE

    @property
    def web_base(self) -> str:
        """The provider's web origin from its API base — GitHub-adapter knowledge.

        Two unrelated derivations for one vendor — an ``api.`` host prefix for public GitHub,
        an ``/api/v3`` path suffix for Enterprise — so neither generalizes."""
        if self.config.web_base:
            return self.config.web_base
        stripped = self.api_base.rstrip("/")
        if stripped.endswith("/api/v3"):
            return stripped[: -len("/api/v3")]
        if "://api." in stripped:
            return stripped.replace("://api.", "://", 1)
        return stripped

    def source(self) -> IWorkSource:
        return GitHubWorkSource(self.client, name=self.config.name, repo=self.config.repo, web_base=self.web_base)
