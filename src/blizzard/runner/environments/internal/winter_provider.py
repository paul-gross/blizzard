"""The winter workspace-provider binding (``bzh:pluggable-seams``).

Implements :class:`~blizzard.runner.environments.provider.IWorkspaceProvider` by
driving the **real** winter CLI against a workspace root — in verification the
``blizzard-mock`` fixture workspace (a real winter workspace over bare ``file://``
origins, implementation/verification.md). The pool is the provider's static config
(D-019); which envs are held it learns from the ``held_ids`` the runner passes in
(D-062) — it keeps no allocation state of its own.

``acquire`` picks a free env, **cleans it first** (reset-on-acquire, D-021: ensure
the env exists via idempotent ``winter ws init <env>``, then hard-reset each repo
worktree to the base branch), and returns the id with its workdir. ``release`` marks
nothing — cleaning happens on the *next* acquire, and the hold was never the
provider's fact to clear. Confined to ``internal/`` (``bzh:dependency-inversion``).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from blizzard.foundation.logging import get_logger
from blizzard.runner.environments.internal.git import SubprocessEnvGit
from blizzard.runner.environments.internal.winter_cli import SubprocessWinterCli
from blizzard.runner.environments.provider import (
    AcquiredEnvironment,
    IWorkspaceProvider,
    WorkspaceAcquisitionError,
)

_log = get_logger("blizzard.runner.env")


class WinterWorkspaceProvider:
    """The winter binding — real ``winter ws init`` + reset-on-acquire over a fixture."""

    def __init__(
        self,
        workspace_root: str,
        *,
        env_pool: Sequence[str],
        base_branch: str = "main",
        winter: SubprocessWinterCli | None = None,
        git: SubprocessEnvGit | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root)
        self._pool = tuple(env_pool)
        self._base_branch = base_branch
        self._winter = winter if winter is not None else SubprocessWinterCli()
        self._git = git if git is not None else SubprocessEnvGit()
        self._ready = False

    def acquire(self, chunk_id: str, count: int, held_ids: list[str]) -> list[AcquiredEnvironment]:
        free = [env for env in self._pool if env not in set(held_ids)]
        if len(free) < count:
            raise WorkspaceAcquisitionError(
                f"pool exhausted: need {count}, {len(free)} free (pool={list(self._pool)}, held={held_ids})"
            )
        acquired: list[AcquiredEnvironment] = []
        for env in free[:count]:
            workdir = self._prepare(env)
            acquired.append(AcquiredEnvironment(environment_id=env, workdir=str(workdir)))
        _log.info("acquired environments", chunk_id=chunk_id, envs=[a.environment_id for a in acquired])
        return acquired

    def release(self, environment_id: str) -> None:
        # No-op mark (D-062): cleaning defers to the next acquire; the hold is a
        # runner-store fact, never the provider's to clear.
        _log.info("released environment (no-op mark)", environment_id=environment_id)

    def _prepare(self, env: str) -> Path:
        """Reset-on-acquire: ensure the env exists, then clean it to the base (D-021)."""
        if not self._ready:
            self._winter.ensure_ready(self._workspace_root)
            self._ready = True
        self._winter.run(self._workspace_root, ["ws", "init", env])
        workdir = self._workspace_root / env
        self._git.reset_environment(workdir, self._base_branch)
        return workdir


def _conforms_workspace_provider(x: WinterWorkspaceProvider) -> IWorkspaceProvider:
    return x
