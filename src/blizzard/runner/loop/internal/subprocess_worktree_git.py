"""Subprocess-git adapter for the worker-artifact seam (package-private).

A read-only confirmation, via the real ``git`` CLI, of an already-pushed git-commit
declaration (issue #143). All ``subprocess`` usage is confined here, and a git failure is
wrapped once into :class:`WorktreeGitError` and logged (``bzh:structlog-logging``).
"""

from __future__ import annotations

import subprocess

from blizzard.foundation.logging import get_logger
from blizzard.runner.loop.worktree import IWorktreeGit

_log = get_logger("blizzard.runner.worktree")

# A tick reaches this seam (issue #143), so it must be bounded — the value is generous
# (a remote round-trip, not a build) rather than tuned, mirroring `checks.py`'s own default.
DEFAULT_WORKTREE_GIT_TIMEOUT = 60


class WorktreeGitError(RuntimeError):
    """A git operation against a leased worktree failed."""


class SubprocessWorktreeGit:
    """Read-only confirmation of a declared git commit, via the real ``git`` CLI."""

    def verify(self, origin_url: str, branch: str, commit: str) -> bool:
        out = self._git("ls-remote", origin_url, branch)
        # `git ls-remote <url> <branch>` prints "<sha>\trefs/heads/<branch>" or nothing
        # if the ref is absent — the first whitespace-delimited token is the sha.
        line = out.strip().splitlines()[0] if out.strip() else ""
        remote_sha = line.split()[0] if line else ""
        if remote_sha != commit:
            _log.warning(
                "git-commit declaration ref mismatch",
                origin_url=origin_url,
                branch=branch,
                declared_commit=commit,
                remote_commit=remote_sha or None,
            )
            return False
        return True

    # --- plumbing -----------------------------------------------------------

    def _git(self, *args: str) -> str:
        # No `-C`: `ls-remote <url>` is answered by the remote, so this runs without a
        # local repository at all.
        try:
            result = subprocess.run(
                ["git", *args],
                capture_output=True,
                text=True,
                timeout=DEFAULT_WORKTREE_GIT_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            _log.error("git timed out", args=list(args), timeout=DEFAULT_WORKTREE_GIT_TIMEOUT)
            raise WorktreeGitError(f"git {' '.join(args)} timed out after {DEFAULT_WORKTREE_GIT_TIMEOUT}s") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            _log.error("git failed", args=list(args), detail=detail)
            raise WorktreeGitError(f"git {' '.join(args)} failed: {detail}")
        return result.stdout


def _conforms_worktree_git(x: SubprocessWorktreeGit) -> IWorktreeGit:
    return x
