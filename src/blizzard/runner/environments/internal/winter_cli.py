"""Drive the real winter CLI against a workspace root (package-private).

The winter binding acquires environments by running the actual ``winter`` CLI
(``winter ws init <env>``) — winter is the reference workspace binding and its real
behavior is exactly what verification must *see* (implementation/verification.md).
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
        cli = workspace_root / "tools" / "winter-cli"
        if cli.is_dir():
            cmd = ["mise", "-C", str(cli), "exec", "--", "uv", "run", "--project", str(cli), "winter", *args]
            self._run(cmd, cwd=cli, what=f"winter {' '.join(args)}")
        else:
            self._run(["winter", *args], cwd=workspace_root, what=f"winter {' '.join(args)}")
        _log.info("winter run", workspace_root=str(workspace_root), args=list(args))

    @staticmethod
    def _run(cmd: list[str], *, cwd: Path, what: str) -> None:
        result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
        if result.returncode != 0:
            tail = (result.stderr or result.stdout).strip()[-2000:]
            _log.error("winter failed", what=what, exit_code=result.returncode, detail=tail)
            raise WinterCliError(f"{what} failed ({result.returncode}): {tail}")
