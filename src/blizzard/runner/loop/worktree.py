"""The worker-artifact git seam — read-only verify of what a build declared (issue
#143, Phase 4).

The worker (not the runner) commits its work to a branch in a leased repo worktree,
pushes that branch to the forge, and declares the resulting ``(forge, repo, branch,
commit)`` through the runner's local declaration channel (Phase 3). ADVANCE reads
those durable declarations back and, for each, confirms them **read-only** against the
leased environment's own repo worktree — never inferring a branch name off git residue
(the ``git rev-parse --abbrev-ref HEAD`` inference this seam used to perform returned
the literal string ``HEAD`` in a detached worktree, wedging the push it drove) and
never mutating git itself (the push responsibility moved to the worker seam). The
subprocess-git adapter under ``internal/`` is the reference binding, and loop tests
inject a fake.
"""

from __future__ import annotations

from typing import Protocol


class IWorktreeGit(Protocol):
    """Read-only confirmation of a worker's git-commit declaration."""

    def verify(self, repo_workdir: str, forge: str, branch: str, commit: str) -> bool:
        """``True`` iff ``forge`` matches ``repo_workdir``'s own ``origin`` remote
        (compared cosmetically-normalized — a trailing ``/`` or ``.git`` on either side
        does not defeat the match) AND ``branch`` resolves, at that same origin, to
        ``commit`` — a read-only ``git remote get-url origin`` + ``git ls-remote origin
        <branch>``, nothing mutated. ``False`` on any mismatch (wrong forge, absent ref,
        ref pointing elsewhere); a hard failure to even reach the check (no such
        worktree, no network) raises :class:`~blizzard.runner.loop.internal.
        subprocess_worktree_git.WorktreeGitError` rather than returning ``False`` — the
        caller tells "verified false" from "could not verify" apart."""
        ...
