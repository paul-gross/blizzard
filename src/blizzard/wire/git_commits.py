"""``blizzard runner artifact commit`` — wire body (issue #143, Phase 3).

Behind ``POST /api/leases/{lease_id}/git-commits``. Modeled on ``wire/attachments.py``'s
shapes: a structural sibling of the attach channel for the ``git_commit`` artifact kind,
carrying structured identity rather than content.
"""

from __future__ import annotations

from pydantic import BaseModel


class GitCommitDeclarationRequest(BaseModel):
    """A worker's explicit git-commit declaration for one repo it touched.

    Carries no forge: the origin a declaration is verified against is read from the
    environment's repo manifest, which the workspace provider owns. A worker naming its
    own forge could name the wrong one, and did — the field's default resolved ``origin``
    in the process cwd, which for a worker spawned at the workspace root is the workspace
    repo rather than the repo being declared.

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
