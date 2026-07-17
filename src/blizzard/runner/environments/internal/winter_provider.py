"""The winter workspace-provider binding (``bzh:pluggable-seams``).

Implements :class:`~blizzard.runner.environments.provider.IWorkspaceProvider` by
driving the **real** winter CLI against a workspace root — in verification the
``blizzard-mock`` fixture workspace (a real winter workspace over bare ``file://``
origins, implementation/verification.md). The pool is the provider's static config;
which envs are held it learns from the ``held_ids`` the runner passes in
 — it keeps no allocation state of its own.

``acquire`` picks a free env and performs a **full reset-to-base** (reset-on-acquire,
D-021): refresh standalones once per pass, then per env fetch → forced base checkout →
disconnect → membership reconcile → untracked-file clean → service teardown →
reprovision. The forced-checkout-then-disconnect ordering is the point: running
``winter ws init`` against a previous tenant's stale feature-branch tracking re-infers
a dead upstream and fails, stalling FILL. A step failure aborts the acquire as an
:class:`EnvironmentPreparationError` naming the step and env — never a half-reset env.
``release`` marks nothing — cleaning happens on the *next* acquire, and the hold was
never the provider's fact to clear. Confined to ``internal/``
(``bzh:dependency-inversion``).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from blizzard.foundation.logging import get_logger
from blizzard.runner.environments.internal.git import SubprocessEnvGit
from blizzard.runner.environments.internal.winter_cli import SubprocessWinterCli
from blizzard.runner.environments.provider import (
    AcquiredEnvironment,
    EnvironmentPreparationError,
    IWorkspaceProvider,
    WorkspaceAcquisitionError,
)

_log = get_logger("blizzard.runner.env")

# The step name attached to pass-scoped (not per-env) preparation failures.
_WORKSPACE_SCOPE = "workspace"


class _WinterCli(Protocol):
    """The winter-CLI sub-seam the provider drives (the real CLI, or a test fake)."""

    def ensure_ready(self, workspace_root: Path) -> None: ...
    def run(self, workspace_root: Path, args: Sequence[str]) -> None: ...
    def capture(self, workspace_root: Path, args: Sequence[str]) -> str: ...


class _EnvGit(Protocol):
    """The untracked-file-clean sub-seam the provider drives."""

    def clean_environment(self, env_workdir: Path) -> None: ...


class WinterWorkspaceProvider:
    """The winter binding — full winter-driven reset-on-acquire over a real workspace."""

    def __init__(
        self,
        workspace_root: str,
        *,
        env_pool: Sequence[str],
        base_branch: str = "main",
        winter: _WinterCli | None = None,
        git: _EnvGit | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root)
        self._pool = tuple(env_pool)
        self._base_branch = base_branch
        self._winter = winter if winter is not None else SubprocessWinterCli()
        self._git = git if git is not None else SubprocessEnvGit()
        self._ready = False
        self._service_bound: bool | None = None

    def acquire(self, chunk_id: str, count: int, held_ids: list[str]) -> list[AcquiredEnvironment]:
        free = [env for env in self._pool if env not in set(held_ids)]
        if len(free) < count:
            raise WorkspaceAcquisitionError(
                f"pool exhausted: need {count}, {len(free)} free (pool={list(self._pool)}, held={held_ids})"
            )
        if not self._ready:
            self._winter.ensure_ready(self._workspace_root)
            self._ready = True
        # Standalone repos are workspace-global, so refresh them once per reset
        # pass rather than repeating the pull for every env in the pass.
        self._step(
            _WORKSPACE_SCOPE,
            "refresh-standalones",
            lambda: self._winter.run(self._workspace_root, ["ws", "pull", "--standalone"]),
        )
        acquired: list[AcquiredEnvironment] = []
        for env in free[:count]:
            workdir = self._prepare(env)
            acquired.append(AcquiredEnvironment(environment_id=env, workdir=str(workdir)))
        _log.info("acquired environments", chunk_id=chunk_id, envs=[a.environment_id for a in acquired])
        return acquired

    def release(self, environment_id: str) -> None:
        # No-op mark: cleaning defers to the next acquire; the hold is a
        # runner-store fact, never the provider's to clear.
        _log.info("released environment (no-op mark)", environment_id=environment_id)

    def _prepare(self, env: str) -> Path:
        """Reset-on-acquire: return the env fully reset to base and working."""
        workdir = self._workspace_root / env
        run = self._winter.run
        root = self._workspace_root
        if not workdir.is_dir():
            # First acquire of a pool env: init materializes it fresh off the base —
            # already clean and disconnected, so the reset steps have nothing to undo.
            self._step(env, "init", lambda: run(root, ["ws", "init", env]))
        else:
            self._step(env, "fetch", lambda: run(root, ["ws", "fetch", env]))
            # Forced base checkout, then disconnect — the all-or-nothing reset that
            # discards whatever branch/commit/tracking state the last tenant left.
            self._step(env, "checkout-base", lambda: run(root, ["ws", "checkout", env, self._base_branch, "--force"]))
            self._step(env, "disconnect", lambda: run(root, ["ws", "disconnect", env]))
            # Membership reconcile (newly-declared repos get worktrees) runs *after*
            # the disconnect: init's upstream inference has no connected sibling left
            # to re-infer a stale feature branch from.
            self._step(env, "init", lambda: run(root, ["ws", "init", env]))
        # Winter's forced checkout hard-resets tracked state but leaves untracked
        # files; the clean is the one remaining raw-git step.
        self._step(env, "clean", lambda: self._git.clean_environment(workdir))
        if self._service_orchestrator_bound():
            # The previous tenant's services must die before reprovision brings the
            # env back to a working state against the fresh tree.
            self._step(env, "service-down", lambda: run(root, ["service", "down", env]))
        self._step(env, "provision", lambda: run(root, ["provision", env]))
        return workdir

    def _service_orchestrator_bound(self) -> bool:
        """Whether the workspace binds a service orchestrator, read once per provider.

        ``winter service down`` exits non-zero in a workspace with no orchestrator
        (the fixture workspace, minimal setups) — skipping on an unbound slot is a
        config fact read up front, not an error swallowed at teardown time.
        """
        if self._service_bound is None:

            def probe() -> None:
                raw = self._winter.capture(self._workspace_root, ["capabilities", "--json"])
                slots = json.loads(raw)
                self._service_bound = any(slot.get("slot") == "service" and slot.get("bound") for slot in slots)

            self._step(_WORKSPACE_SCOPE, "service-probe", probe)
        return bool(self._service_bound)

    def _step(self, env: str, step: str, action: Callable[[], None]) -> None:
        """Run one reset step; a failure aborts the acquire, attributed to (step, env)."""
        try:
            action()
        except Exception as exc:
            _log.error("reset-on-acquire step failed", environment_id=env, step=step, detail=str(exc))
            raise EnvironmentPreparationError(
                f"reset-on-acquire step {step!r} failed for env {env!r}: {exc}",
                environment_id=env,
                step=step,
            ) from exc


def _conforms_workspace_provider(x: WinterWorkspaceProvider) -> IWorkspaceProvider:
    return x
