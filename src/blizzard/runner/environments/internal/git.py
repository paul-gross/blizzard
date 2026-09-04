"""Git reset-on-acquire plumbing for the winter binding (package-private).

Removes the previous tenant's **untracked** files — the one reset step winter has no verb
for. ``-fd``, not ``-fdx``: ignored files stay, since the dependency trees they hold cost more
to rebuild than the tick allows (tests/test_pin_runner_misc.py). ``subprocess`` is confined here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from blizzard.foundation.logging import get_logger

_log = get_logger("blizzard.runner.env.git")

# A tick reaches this seam (FILL), so it must be bounded — the value is generous rather
# than tuned, mirroring `checks.py`'s own default.
ENV_GIT_TIMEOUT = 60


class EnvGitError(RuntimeError):
    """A git reset-on-acquire operation failed."""


class SubprocessEnvGit:
    """Remove untracked files from each repo worktree in a feature env."""

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
        return self._capture(repo_workdir, "remote", "get-url", "origin").strip()

    def _git(self, cwd: Path, *args: str) -> None:
        self._capture(cwd, *args)

    def _capture(self, cwd: Path, *args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=ENV_GIT_TIMEOUT
            )
        except subprocess.TimeoutExpired as exc:
            _log.error("git reset step timed out", args=list(args), cwd=str(cwd), timeout=ENV_GIT_TIMEOUT)
            raise EnvGitError(f"git {' '.join(args)} timed out in {cwd} after {ENV_GIT_TIMEOUT}s") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            _log.error("git reset step failed", args=list(args), cwd=str(cwd), detail=detail)
            raise EnvGitError(f"git {' '.join(args)} failed in {cwd}: {detail}")
        return result.stdout
