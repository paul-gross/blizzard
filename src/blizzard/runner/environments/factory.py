"""Workspace-binding selection: the one place a ``workspace_provider`` name is read.

A name→builder registry shared by the runner's two composition roots. Config
validation reads its names; everything downstream asks the built provider for its facts
(``spawn_root``/``capacity``/``pool``) rather than comparing the name."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from blizzard.foundation.roles import domain_model
from blizzard.runner.environments.provider import IWorkspaceProvider, WorkspaceRepo


@domain_model
@dataclass(frozen=True)
class WorkspaceSettings:
    """What a workspace binding is built from: the ``provider`` name, the runner ``root``, the
    authored and the resolved workspace roots, the basic binding's ``repos`` and
    ``max_environments``, the shared ``base_branch``, and the winter binding's ``env_pool``."""

    provider: str
    root: Path
    workspace_root: str
    effective_workspace_root: str
    repos: tuple[WorkspaceRepo, ...]
    max_environments: int
    base_branch: str
    env_pool: tuple[str, ...]


type HeldIds = Callable[[], list[str]] | None
type WorkspaceProviderBuilder = Callable[[WorkspaceSettings, HeldIds], IWorkspaceProvider]


def _build_basic(settings: WorkspaceSettings, held_ids: HeldIds) -> IWorkspaceProvider:
    # Imported at build time: a binding loads only once it is the one selected.
    from blizzard.runner.environments.internal.basic_provider import BasicWorkspaceProvider

    return BasicWorkspaceProvider(
        settings.effective_workspace_root,
        repos=settings.repos,
        max_environments=settings.max_environments,
        base_branch=settings.base_branch,
        held_ids=held_ids,
    )


def _build_winter(settings: WorkspaceSettings, held_ids: HeldIds) -> IWorkspaceProvider:
    from blizzard.runner.environments.internal.winter_provider import WinterWorkspaceProvider

    return WinterWorkspaceProvider(
        settings.workspace_root or str(settings.root),
        env_pool=settings.env_pool,
        base_branch=settings.base_branch,
        spawn_root=settings.workspace_root,
    )


WORKSPACE_PROVIDERS: Mapping[str, WorkspaceProviderBuilder] = {"basic": _build_basic, "winter": _build_winter}


def build_workspace_provider(settings: WorkspaceSettings, *, held_ids: HeldIds = None) -> IWorkspaceProvider:
    return WORKSPACE_PROVIDERS[settings.provider](settings, held_ids)
