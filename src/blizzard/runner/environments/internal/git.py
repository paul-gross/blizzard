"""Git plumbing shared by the workspace bindings (package-private).

Removes the previous tenant's **untracked** files. Ignored files stay: the dependency trees
they hold cost more to rebuild than the tick allows.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from blizzard.foundation.logging import get_logger

_log = get_logger("blizzard.runner.env.git")

# A FILL tick reaches this seam, so git calls must be bounded.
ENV_GIT_TIMEOUT = 60


class EnvGitError(RuntimeError):
    """A git operation the workspace bindings drive failed."""


class SubprocessEnvGit:
    """Run git for the workspace bindings: clean untracked files, read an origin, or capture any call."""

    def __init__(self, timeout: float = ENV_GIT_TIMEOUT) -> None:
        self._timeout = timeout

    def clean_environment(self, env_workdir: Path) -> None:
        """``git clean -fd`` every repo worktree under ``env_workdir``."""
        for child in sorted(env_workdir.iterdir()):
            if not (child / ".git").exists():
                continue
            self._git(child, "clean", "-fd")
        _log.info("environment cleaned of untracked files", env_workdir=str(env_workdir))

    def origin_url(self, repo_workdir: Path) -> str:
        """``git remote get-url origin`` read **in the repo's own worktree**.

        Git walks *up* from cwd to find an enclosing repository, so standing anywhere else
        yields a plausible-looking URL for another repo (tests/test_pin_runner_misc.py).
        """
        return self.capture(repo_workdir, "remote", "get-url", "origin").strip()

    def _git(self, cwd: Path, *args: str) -> None:
        self.capture(cwd, *args)

    def capture(self, cwd: Path, *args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=self._timeout
            )
        except subprocess.TimeoutExpired as exc:
            _log.error("git command timed out", args=list(args), cwd=str(cwd), timeout=self._timeout)
            raise EnvGitError(f"git {' '.join(args)} timed out in {cwd} after {self._timeout}s") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            _log.error("git command failed", args=list(args), cwd=str(cwd), detail=detail)
            raise EnvGitError(f"git {' '.join(args)} failed in {cwd}: {detail}")
        return result.stdout
