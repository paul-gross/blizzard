"""Workspace-binding selection: the one place a ``workspace_provider`` name is read.

A name→builder registry shared by the runner's two composition roots."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from blizzard.foundation.roles import domain_model
from blizzard.runner.environments.provider import IWorkspaceProvider, WorkspaceRepo
from blizzard.runner.process.child_env import ChildEnv
from blizzard.runner.process.child_processes import IChildProcesses


@domain_model
@dataclass(frozen=True)
class WorkspaceSettings:
    """What a workspace binding is built from: the ``provider`` name, the runner ``root``, the
    authored and the resolved workspace roots, the ``repos`` an environment holds, the
    ``max_environments`` cap, the ``base_branch``, and the ``env_pool`` of named environments."""

    provider: str
    root: Path
    workspace_root: str
    effective_workspace_root: str
    repos: tuple[WorkspaceRepo, ...]
    max_environments: int
    base_branch: str
    env_pool: tuple[str, ...]


@dataclass(frozen=True)
class WorkspaceLaunch:
    """How a workspace binding launches its ``git`` and ``winter`` children: the runner's launch seam under its tool
    profile."""

    children: IChildProcesses
    env: ChildEnv


type HeldIds = Callable[[], list[str]] | None
type WorkspaceProviderBuilder = Callable[[WorkspaceSettings, HeldIds, WorkspaceLaunch], IWorkspaceProvider]


def _build_basic(settings: WorkspaceSettings, held_ids: HeldIds, launch: WorkspaceLaunch) -> IWorkspaceProvider:
    # Imported at build time: a binding loads only once it is the one selected.
    from blizzard.runner.environments.internal.basic_provider import CLONE_TIMEOUT, BasicWorkspaceProvider
    from blizzard.runner.environments.internal.git import SubprocessEnvGit

    return BasicWorkspaceProvider(
        settings.effective_workspace_root,
        repos=settings.repos,
        max_environments=settings.max_environments,
        base_branch=settings.base_branch,
        held_ids=held_ids,
        git=SubprocessEnvGit(children=launch.children, env=launch.env, timeout=CLONE_TIMEOUT),
    )


def _build_winter(settings: WorkspaceSettings, held_ids: HeldIds, launch: WorkspaceLaunch) -> IWorkspaceProvider:
    from blizzard.runner.environments.internal.git import SubprocessEnvGit
    from blizzard.runner.environments.internal.winter_cli import SubprocessWinterCli
    from blizzard.runner.environments.internal.winter_provider import WinterWorkspaceProvider

    return WinterWorkspaceProvider(
        settings.workspace_root or str(settings.root),
        env_pool=settings.env_pool,
        base_branch=settings.base_branch,
        spawn_root=settings.workspace_root,
        winter=SubprocessWinterCli(children=launch.children, env=launch.env),
        git=SubprocessEnvGit(children=launch.children, env=launch.env),
    )


WORKSPACE_PROVIDERS: Mapping[str, WorkspaceProviderBuilder] = {"basic": _build_basic, "winter": _build_winter}


def build_workspace_provider(
    settings: WorkspaceSettings, *, launch: WorkspaceLaunch, held_ids: HeldIds = None
) -> IWorkspaceProvider:
    return WORKSPACE_PROVIDERS[settings.provider](settings, held_ids, launch)
