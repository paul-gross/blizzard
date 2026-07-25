"""``blizzard runner artifact commit`` — wire body (issue #143, Phase 3).

Behind ``POST /api/leases/{lease_id}/git-commits``. Modeled on ``wire/attachments.py``'s
shapes: a structural sibling of the attach channel for the ``git_commit`` artifact kind,
carrying structured identity rather than content.
"""

from __future__ import annotations

from pydantic import BaseModel


class GitCommitDeclarationRequest(BaseModel):
    """A worker's explicit git-commit declaration for one repo it touched.

    ``forge`` is worker-declared (decision R7): the runner cross-checks it against the
    leased env's own ``origin`` during its later read-only verify (Phase 4) rather than
    stamping it itself."""

    forge: str
    repo: str
    branch: str
    commit: str


class GitCommitDeclarationResponse(BaseModel):
    """``POST /api/leases/{lease_id}/git-commits`` — the declaration landed durably."""

    recorded: bool
    lease_id: str
    repo: str
