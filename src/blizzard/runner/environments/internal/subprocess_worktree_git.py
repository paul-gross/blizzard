"""Subprocess-git adapter for the worker-artifact seam (package-private).

A read-only confirmation, via the real ``git`` CLI, of an already-pushed git-commit
declaration. The git run is the shared one in :mod:`~blizzard.runner.environments.internal.git`, which logs a
failure once; it is wrapped here into :class:`WorktreeGitError`.
"""

from __future__ import annotations

from blizzard.foundation.logging import get_logger
from blizzard.runner.environments.internal.git import EnvGitError, SubprocessEnvGit
from blizzard.runner.environments.worktree import IWorktreeGit, WorktreeGitError

_log = get_logger("blizzard.runner.worktree")

# A tick reaches this seam, so it must be bounded — the value is generous
# (a remote round-trip, not a build) rather than tuned, as in src/blizzard/runner/lifecycle/judgement/checks.py.
WORKTREE_GIT_TIMEOUT = 60


class SubprocessWorktreeGit:
    """Read-only confirmation of a declared git commit, via the real ``git`` CLI."""

    def __init__(self) -> None:
        self._runner = SubprocessEnvGit(WORKTREE_GIT_TIMEOUT)

    def verify(self, origin_url: str, branch: str, commit: str) -> bool:
        ref = f"refs/heads/{branch}"
        out = self._git("ls-remote", origin_url, ref)
        # `git ls-remote` tail-matches its pattern, so `other/<branch>` answers for `<branch>`;
        # only the line naming exactly `refs/heads/<branch>` is the declared branch.
        remote_sha = ""
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref:
                remote_sha = parts[0]
                break
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
        try:
            return self._runner.capture(None, *args)
        except EnvGitError as exc:
            raise WorktreeGitError(str(exc)) from exc


def _conforms_worktree_git(x: SubprocessWorktreeGit) -> IWorktreeGit:
    return x
