"""Git reset-on-acquire plumbing for the winter binding (package-private).

Reset-on-acquire drives winter's cross-repo
verbs for everything winter can express — fetch, forced base checkout, disconnect.
What remains here is the one step winter has no verb for: removing the previous
tenant's **untracked** files (``winter ws checkout --force`` hard-resets tracked
state but never runs ``git clean``). ``-fdx``, not ``-fd``: build artifacts and
installed deps go with the outgoing tenant, and the reprovision step that follows
restores them. All ``subprocess`` usage is confined here.

Pinned by tests/test_pin_runner_misc.py::test_the_clean_sweeps_ignored_build_artifacts_out_with_the_outgoing_tenant.
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

    def origin_url(self, repo_workdir: Path) -> str:
        """``git remote get-url origin`` read **in the repo's own worktree**.

        Takes the worktree path rather than running in the process cwd: git walks *up*
        from cwd to find an enclosing repository, so a caller standing anywhere else
        would get a plausible-looking URL for some other repo instead of an error.

        Pinned by tests/test_pin_runner_misc.py::test_origin_url_reads_the_named_worktree_not_the_process_cwd.
        """
        return self._capture(repo_workdir, "remote", "get-url", "origin").strip()

    def _git(self, cwd: Path, *args: str) -> None:
        self._capture(cwd, *args)

    def _capture(self, cwd: Path, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            _log.error("git reset step failed", args=list(args), cwd=str(cwd), detail=detail)
            raise EnvGitError(f"git {' '.join(args)} failed in {cwd}: {detail}")
        return result.stdout
