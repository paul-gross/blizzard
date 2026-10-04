"""Workspace-binding selection: the one place a ``workspace_provider`` name is read.

A name→builder registry shared by the runner's two composition roots. Config
validation reads its names; everything downstream asks the built provider for its facts
(``spawn_root``/``capacity``/``pool``) rather than comparing the name."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from blizzard.runner.environments.provider import IWorkspaceProvider

if TYPE_CHECKING:
    from blizzard.runner.config import RunnerConfig

type HeldIds = Callable[[], list[str]] | None
type WorkspaceProviderBuilder = Callable[[RunnerConfig, HeldIds], IWorkspaceProvider]


def _build_basic(config: RunnerConfig, held_ids: HeldIds) -> IWorkspaceProvider:
    # Imported at build time: the binding's module reads config types, and config reads this registry.
    from blizzard.runner.environments.internal.basic_provider import BasicWorkspaceProvider

    return BasicWorkspaceProvider(
        config.effective_workspace_root,
        repos=config.workspace_repos,
        max_environments=config.max_environments,
        base_branch=config.base_branch,
        held_ids=held_ids,
    )


def _build_winter(config: RunnerConfig, held_ids: HeldIds) -> IWorkspaceProvider:
    from blizzard.runner.environments.internal.winter_provider import WinterWorkspaceProvider

    return WinterWorkspaceProvider(
        config.workspace_root or str(config.root),
        env_pool=config.workspace_envs,
        base_branch=config.base_branch,
        spawn_root=config.workspace_root,
    )


WORKSPACE_PROVIDERS: Mapping[str, WorkspaceProviderBuilder] = {"basic": _build_basic, "winter": _build_winter}


def build_workspace_provider(config: RunnerConfig, *, held_ids: HeldIds = None) -> IWorkspaceProvider:
    return WORKSPACE_PROVIDERS[config.workspace_provider](config, held_ids)
