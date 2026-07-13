"""Git reset-on-acquire plumbing for the winter binding (package-private).

Reset-on-acquire (D-021, design/runner/environments.md): every environment
``acquire`` returns is clean, and cleaning happens on the *acquiring* side because a
crash mid-work leaves the previous holder's environment mid-surgery. This resets
each repo worktree in a winter feature env back to the base branch — discarding
whatever branch, commit, or dirty file the last tenant left — so the next worker
starts from a known-clean tree. All ``subprocess`` usage is confined here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from blizzard.foundation.logging import get_logger

_log = get_logger("blizzard.runner.env.git")


class EnvGitError(RuntimeError):
    """A git reset-on-acquire operation failed."""


class SubprocessEnvGit:
    """Reset each repo worktree in a feature env to its base ``origin`` ref."""

    def reset_environment(self, env_workdir: Path, base_branch: str) -> None:
        """Hard-reset and clean every repo worktree under ``env_workdir`` to ``origin/<base>``."""
        for child in sorted(env_workdir.iterdir()):
            if not (child / ".git").exists():
                continue
            self._git(child, "fetch", "origin")
            self._git(child, "reset", "--hard", f"origin/{base_branch}")
            self._git(child, "clean", "-fdx")
        _log.info("environment reset to base", env_workdir=str(env_workdir), base=base_branch)

    def _git(self, cwd: Path, *args: str) -> None:
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            _log.error("git reset step failed", args=list(args), cwd=str(cwd), detail=detail)
            raise EnvGitError(f"git {' '.join(args)} failed in {cwd}: {detail}")
