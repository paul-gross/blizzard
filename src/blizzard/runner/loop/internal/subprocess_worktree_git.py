"""Subprocess-git adapter for the worker-artifact seam (package-private).

The reference :class:`~blizzard.runner.loop.worktree.IWorktreeGit` binding: it
discovers, in a leased winter environment, the repo worktrees a build worker pushed
its work into (HEAD ahead of the base's ``origin`` ref) and pushes those branches to
their ``file://`` origins — the same origins the mock forge fronts (one git truth).
All ``subprocess`` usage is confined here; a git failure is wrapped once into
:class:`~blizzard.runner.loop.worktree` errors and logged (``bzh:structlog-logging``).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from blizzard.foundation.logging import get_logger
from blizzard.runner.loop.worktree import GitArtifact, IWorktreeGit

_log = get_logger("blizzard.runner.worktree")


class WorktreeGitError(RuntimeError):
    """A git operation against a leased worktree failed."""


class SubprocessWorktreeGit:
    """Discover produced commits and push their branches, via the real ``git`` CLI."""

    def find_produced_artifacts(self, env_workdir: str, base_branch: str) -> list[GitArtifact]:
        root = Path(env_workdir)
        if not root.is_dir():
            return []
        artifacts: list[GitArtifact] = []
        for child in sorted(root.iterdir()):
            if not (child / ".git").exists():
                continue
            if self._commits_ahead(child, base_branch) <= 0:
                continue
            artifacts.append(
                GitArtifact(
                    repo=child.name,
                    branch_name=self._current_branch(child),
                    commit_hash=self._head(child),
                    repo_workdir=str(child),
                )
            )
        return artifacts

    def push(self, repo_workdir: str, branch_name: str) -> None:
        self._git(Path(repo_workdir), "push", "origin", branch_name)
        _log.info("pushed work branch", repo_workdir=repo_workdir, branch=branch_name)

    # --- plumbing -----------------------------------------------------------

    def _commits_ahead(self, repo: Path, base_branch: str) -> int:
        out = self._git(repo, "rev-list", "--count", f"origin/{base_branch}..HEAD")
        try:
            return int(out.strip())
        except ValueError:
            return 0

    def _current_branch(self, repo: Path) -> str:
        return self._git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()

    def _head(self, repo: Path) -> str:
        return self._git(repo, "rev-parse", "HEAD").strip()

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
