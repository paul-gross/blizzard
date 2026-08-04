"""``blizzard runner artifact commit`` — wire body (issue #143, Phase 3).

Behind ``POST /api/leases/{lease_id}/git-commits``: the ``git_commit`` artifact kind's
channel, carrying structured identity rather than content.
"""

from __future__ import annotations

from pydantic import BaseModel


class GitCommitDeclarationRequest(BaseModel):
    """A worker's explicit git-commit declaration for one repo it touched.

    Carries no forge (issue #143): the origin a declaration is verified against is read
    from the environment's repo manifest, not named by the worker (pinned by
    tests/test_pin_wire.py::test_git_commit_declaration_carries_no_forge_field).

    ``environment_id`` is optional while a chunk holds exactly one environment (the
    runner infers it); it is required once a chunk holds several, because the same repo
    has a worktree in each and ``repo`` alone no longer identifies one.
    """

    repo: str
    branch: str
    commit: str
    environment_id: str | None = None


class GitCommitDeclarationResponse(BaseModel):
    """``POST /api/leases/{lease_id}/git-commits`` — the declaration landed durably."""

    recorded: bool
    lease_id: str
    repo: str
    environment_id: str
