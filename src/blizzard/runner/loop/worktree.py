"""The worker-artifact git seam — read-only verify of what a build declared (issue
#143, Phase 4).

ADVANCE reads a worker's durable ``(env, repo, branch, commit)`` declarations back and,
for each, confirms them **read-only** against the origin the env's repo manifest names for
that repo — never inferring a branch name off git residue, and never mutating git itself.
The subprocess-git adapter under ``internal/`` is the reference binding, and loop tests
inject a fake.

The check is deliberately **remote-only**: the origin comes from the provider's repo
manifest and no local checkout is consulted, so the seam asks the one load-bearing
question — does this branch, at this origin, point at this commit? Pinned by
``tests/test_runner_gates.py::test_runner_config_gate_buffers_a_decision_not_a_completion``
(the manifest origin is what reaches ``verify``) and
``tests/test_subprocess_worktree_git.py::test_verify_confirms_a_declared_commit_against_a_detached_head_worktree``
(no working directory is read or mutated).
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
