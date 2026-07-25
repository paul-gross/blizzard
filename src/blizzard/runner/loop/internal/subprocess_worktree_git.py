"""Subprocess-git adapter for the worker-artifact seam (package-private).

The reference :class:`~blizzard.runner.loop.worktree.IWorktreeGit` binding: a
read-only confirmation, via the real ``git`` CLI, of a git-commit declaration the
worker already pushed (issue #143, Phase 4). All ``subprocess`` usage is confined
here; a git failure is wrapped once into :class:`WorktreeGitError` and logged
(``bzh:structlog-logging``).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from blizzard.foundation.logging import get_logger
from blizzard.runner.loop.worktree import IWorktreeGit

_log = get_logger("blizzard.runner.worktree")


class WorktreeGitError(RuntimeError):
    """A git operation against a leased worktree failed."""


def _normalize_origin(url: str) -> str:
    """Collapse cosmetic origin-URL variance before comparison: a trailing ``/`` and an
    optional trailing ``.git`` suffix. Both sides of the ``verify`` comparison run
    through this — the declared ``forge`` and the observed ``origin`` — so
    ``git@github.com:org/repo.git`` and ``git@github.com:org/repo`` (or either with a
    trailing slash) verify equal. Deliberately does **not** lowercase the host: a
    case-sensitive path segment (self-hosted forges, case-sensitive filesystems behind
    a ``file://`` remote) can legitimately differ only by case, so blind lowercasing
    would trade one false-negative class for a false-positive one."""
    stripped = url.strip().rstrip("/")
    if stripped.endswith(".git"):
        stripped = stripped[: -len(".git")]
    return stripped


class SubprocessWorktreeGit:
    """Read-only confirmation of a declared git commit, via the real ``git`` CLI."""

    def verify(self, repo_workdir: str, forge: str, branch: str, commit: str) -> bool:
        origin = self._git(Path(repo_workdir), "remote", "get-url", "origin").strip()
        if _normalize_origin(origin) != _normalize_origin(forge):
            _log.warning(
                "git-commit declaration forge mismatch",
                repo_workdir=repo_workdir,
                declared_forge=forge,
                origin=origin,
            )
            return False
        out = self._git(Path(repo_workdir), "ls-remote", "origin", branch)
        # `git ls-remote origin <branch>` prints "<sha>\trefs/heads/<branch>" or nothing
        # if the ref is absent — the first whitespace-delimited token is the sha.
        line = out.strip().splitlines()[0] if out.strip() else ""
        remote_sha = line.split()[0] if line else ""
        if remote_sha != commit:
            _log.warning(
                "git-commit declaration ref mismatch",
                repo_workdir=repo_workdir,
                branch=branch,
                declared_commit=commit,
                remote_commit=remote_sha or None,
            )
            return False
        return True

    # --- plumbing -----------------------------------------------------------

    def _git(self, cwd: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            _log.error("git failed", args=list(args), cwd=str(cwd), detail=detail)
            raise WorktreeGitError(f"git {' '.join(args)} failed in {cwd}: {detail}")
        return result.stdout


def _conforms_worktree_git(x: SubprocessWorktreeGit) -> IWorktreeGit:
    return x
