"""Production workspace binding shared by the runner's two composition roots."""

from collections.abc import Callable

from blizzard.runner.config import RunnerConfig
from blizzard.runner.environments.internal.basic_provider import BasicWorkspaceProvider
from blizzard.runner.environments.internal.winter_provider import WinterWorkspaceProvider
from blizzard.runner.environments.provider import IWorkspaceProvider


def build_workspace_provider(
    config: RunnerConfig, *, held_ids: Callable[[], list[str]] | None = None
) -> IWorkspaceProvider:
    if config.workspace_provider == "basic":
        return BasicWorkspaceProvider(
            config.effective_workspace_root,
            repos=config.workspace_repos,
            max_environments=config.max_environments,
            base_branch=config.base_branch,
            held_ids=held_ids,
        )
    return WinterWorkspaceProvider(
        config.workspace_root or str(config.root), env_pool=config.workspace_envs, base_branch=config.base_branch
    )
