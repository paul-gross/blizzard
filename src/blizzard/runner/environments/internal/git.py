"""Git reset-on-acquire plumbing for the winter binding (package-private).

Reset-on-acquire drives winter's cross-repo
verbs for everything winter can express — fetch, forced base checkout, disconnect.
What remains here is the one step winter has no verb for: removing the previous
tenant's **untracked** files (``winter ws checkout --force`` hard-resets tracked
state but never runs ``git clean``). ``-fdx`` deliberately sweeps ignored files
too — build artifacts and installed deps go with the tenant; the reprovision step
that follows restores them. All ``subprocess`` usage is confined here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from blizzard.foundation.logging import get_logger

_log = get_logger("blizzard.runner.env.git")


class EnvGitError(RuntimeError):
    """A git reset-on-acquire operation failed."""


class SubprocessEnvGit:
    """Remove untracked/ignored files from each repo worktree in a feature env."""

    def clean_environment(self, env_workdir: Path) -> None:
        """``git clean -fdx`` every repo worktree under ``env_workdir``."""
        for child in sorted(env_workdir.iterdir()):
            if not (child / ".git").exists():
                continue
            self._git(child, "clean", "-fdx")
        _log.info("environment cleaned of untracked files", env_workdir=str(env_workdir))

    def _git(self, cwd: Path, *args: str) -> None:
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            _log.error("git reset step failed", args=list(args), cwd=str(cwd), detail=detail)
            raise EnvGitError(f"git {' '.join(args)} failed in {cwd}: {detail}")
