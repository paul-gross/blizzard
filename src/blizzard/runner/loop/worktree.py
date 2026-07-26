"""The worker-artifact git seam — read-only verify of what a build declared (issue
#143, Phase 4).

The worker (not the runner) commits its work to a branch in a leased repo worktree,
pushes that branch to the forge, and declares the resulting ``(env, repo, branch,
commit)`` through the runner's local declaration channel (Phase 3). ADVANCE reads those
durable declarations back and, for each, confirms them **read-only** against the origin
the env's repo manifest names for that repo — never inferring a branch name off git
residue (the ``git rev-parse --abbrev-ref HEAD`` inference this seam used to perform
returned the literal string ``HEAD`` in a detached worktree, wedging the push it drove)
and never mutating git itself (the push responsibility moved to the worker seam). The
subprocess-git adapter under ``internal/`` is the reference binding, and loop tests
inject a fake.

The check is deliberately **remote-only**. It once also compared a worker-supplied forge
against the worktree's ``origin``, which made the seam depend on a local checkout for
what is purely a question about the forge — and made a worker standing in the wrong
directory (workers are spawned at the workspace root, so: always) supply a plausible
wrong URL that failed the comparison silently. With the origin coming from the provider's
manifest there is no second opinion to disagree with, so the seam asks the only question
that was ever load-bearing: does this branch, at this origin, point at this commit?
"""

from __future__ import annotations

from typing import Protocol


class IWorktreeGit(Protocol):
    """Read-only confirmation of a worker's git-commit declaration."""

    def verify(self, origin_url: str, branch: str, commit: str) -> bool:
        """``True`` iff ``branch`` resolves, at ``origin_url``, to ``commit`` — a
        read-only ``git ls-remote <origin_url> <branch>``, nothing mutated and no local
        checkout consulted. ``False`` on a mismatch (absent ref, ref pointing elsewhere);
        a hard failure to even reach the check (unreachable origin, no credentials)
        raises :class:`~blizzard.runner.loop.internal.subprocess_worktree_git.
        WorktreeGitError` rather than returning ``False`` — the caller tells "verified
        false" from "could not verify" apart."""
        ...
