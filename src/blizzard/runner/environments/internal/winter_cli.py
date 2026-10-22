"""Drive the real winter CLI against a workspace root (package-private).

Two invocation shapes, tried in order: the workspace's **own** ``tools/winter-cli`` via
``mise exec -- uv run`` with cwd pinned to it, so winter's cwd-walk-up root resolution
lands on this workspace; then a global ``winter`` binary. Each launch goes through the injected seam under the tool
profile (``bzh:dependency-inversion``); a failure is wrapped once and logged."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from blizzard.foundation.logging import get_logger
from blizzard.runner.process.child_env import ChildEnv
from blizzard.runner.process.child_processes import IChildProcesses

_log = get_logger("blizzard.runner.winter")

# A tick reaches this seam (FILL), so it must be bounded — generous rather than tuned, since
# a winter verb can provision, not just query.
WINTER_CLI_TIMEOUT = 120


class WinterCliError(RuntimeError):
    """A ``winter`` invocation against the workspace failed."""


class SubprocessWinterCli:
    """Run ``winter <args>`` with ``workspace_root`` as the resolved workspace root."""

    def __init__(self, *, children: IChildProcesses, env: ChildEnv) -> None:
        self._children = children
        self._env = env

    def ensure_ready(self, workspace_root: Path) -> None:
        """One-time prep so ``run`` works against a vendored, freshly-cloned CLI."""
        cli = workspace_root / "tools" / "winter-cli"
        if cli.is_dir():
            self._run(["mise", "trust", "--quiet", str(cli / "mise.toml")], cwd=cli, what="mise trust")

    def run(self, workspace_root: Path, args: Sequence[str]) -> None:
        self._invoke(workspace_root, args)
        _log.info("winter run", workspace_root=str(workspace_root), args=list(args))

    def capture(self, workspace_root: Path, args: Sequence[str]) -> str:
        """Run ``winter <args>`` and return its stdout (e.g. ``capabilities --json``)."""
        return self._invoke(workspace_root, args)

    def _invoke(self, workspace_root: Path, args: Sequence[str]) -> str:
        cli = workspace_root / "tools" / "winter-cli"
        if cli.is_dir():
            cmd = ["mise", "-C", str(cli), "exec", "--", "uv", "run", "--project", str(cli), "winter", *args]
            return self._run(cmd, cwd=cli, what=f"winter {' '.join(args)}")
        return self._run(["winter", *args], cwd=workspace_root, what=f"winter {' '.join(args)}")

    def _run(self, cmd: list[str], *, cwd: Path, what: str) -> str:
        result = self._children.run(cmd, cwd=cwd, env=self._env, timeout=WINTER_CLI_TIMEOUT)
        if result.timed_out:
            _log.error("winter timed out", what=what, timeout=WINTER_CLI_TIMEOUT)
            raise WinterCliError(f"{what} timed out after {WINTER_CLI_TIMEOUT}s")
        if result.exit_code != 0:
            tail = (result.stderr or result.stdout).strip()[-2000:]
            _log.error("winter failed", what=what, exit_code=result.exit_code, detail=tail)
            raise WinterCliError(f"{what} failed ({result.exit_code}): {tail}")
        return result.stdout
