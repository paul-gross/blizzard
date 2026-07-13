"""The worker-artifact git seam — discover and push what a build produced (D-026).

A build worker commits its work to a branch in a leased repo worktree; the runner
must push that branch to the ``file://`` origin the forge fronts **before** it
submits the completion (design/runner/loop.md ADVANCE), and it must name the
``git_commit`` artifact (repo, branch, commit) in that submission. The worker does
not report the pointer out-of-band, so the runner derives it by inspecting the
leased environment: any repo worktree whose HEAD is ahead of the base branch is a
produced artifact. This is the seam; the subprocess-git adapter under ``internal/``
is the reference binding, and loop tests inject a fake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GitArtifact:
    """A produced git-commit pointer discovered in a leased environment (D-026)."""

    repo: str  # the project repo name (the worktree directory name)
    branch_name: str
    commit_hash: str
    repo_workdir: str  # absolute path to the repo worktree, for the push


class IWorktreeGit(Protocol):
    """Discover produced commits in an environment and push their branches."""

    def find_produced_artifacts(self, env_workdir: str, base_branch: str) -> list[GitArtifact]:
        """Repo worktrees under ``env_workdir`` whose HEAD is ahead of ``base_branch``."""
        ...

    def push(self, repo_workdir: str, branch_name: str) -> None:
        """Push ``branch_name`` from ``repo_workdir`` to its ``origin`` (the forge's git truth)."""
        ...
