"""Drive the real winter CLI against a workspace root (package-private).

The winter binding acquires environments by running the actual ``winter`` CLI
(``ws init``/``ws fetch``/``ws checkout``/``ws disconnect``/``service down``/
``provision``) — winter is the reference workspace binding and its real behavior
is exactly what verification must *see* (implementation/verification.md).
Two invocation shapes, tried in order:

* the workspace's **own** ``tools/winter-cli`` via ``mise exec -- uv run`` with cwd
  pinned to it, so winter's cwd-walk-up root resolution lands on this workspace —
  the shape the ``blizzard-mock`` fixture ships and the tests drive;
* a global ``winter`` binary as a fallback for a production workspace without a
  vendored CLI.

All ``subprocess`` usage is confined here (``bzh:dependency-inversion``); a failure
is wrapped once and logged (``bzh:structlog-logging``).
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from blizzard.foundation.logging import get_logger

_log = get_logger("blizzard.runner.winter")


class WinterCliError(RuntimeError):
    """A ``winter`` invocation against the workspace failed."""


class SubprocessWinterCli:
    """Run ``winter <args>`` with ``workspace_root`` as the resolved workspace root."""

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

    @staticmethod
    def _run(cmd: list[str], *, cwd: Path, what: str) -> str:
        result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
        if result.returncode != 0:
            tail = (result.stderr or result.stdout).strip()[-2000:]
            _log.error("winter failed", what=what, exit_code=result.returncode, detail=tail)
            raise WinterCliError(f"{what} failed ({result.returncode}): {tail}")
        return result.stdout
