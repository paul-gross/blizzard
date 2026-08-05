"""The worker-artifact git seam — read-only verify of what a build declared (issue
#143, Phase 4).

A declared ``(repo, branch, commit)`` is confirmed **read-only** against a named origin —
never inferring a branch name off git residue, never mutating git, never consulting a
local checkout. The subprocess-git adapter under ``internal/`` is the reference binding."""

from __future__ import annotations

from typing import Protocol


class IWorktreeGit(Protocol):
    """Read-only confirmation of a worker's git-commit declaration."""

    def verify(self, origin_url: str, branch: str, commit: str) -> bool:
        """``True`` iff ``branch`` resolves, at ``origin_url``, to ``commit`` — read-only,
        nothing mutated and no local checkout consulted. ``False`` on a mismatch; a failure
        to reach the check at all raises, so "verified false" and "could not verify"
        stay distinguishable."""
        ...
