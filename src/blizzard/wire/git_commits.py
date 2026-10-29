"""Git-commit declaration wire bodies: the declaration request, naming a commit by structured identity rather than
content, its response, and a lease's declared commits as the lease read serves them."""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.wire.base import WireModel


class GitCommitDeclarationRequest(BaseModel):
    """A worker's explicit git-commit declaration for one repo it touched.

    It names the repo, branch and commit, and no forge. ``environment_id`` names the environment the repo is in,
    required once a chunk holds several."""

    repo: str
    branch: str
    commit: str
    environment_id: str | None = None


class GitCommitDeclarationResponse(BaseModel):
    """``POST /api/leases/{lease_id}/git-commits`` — the declaration landed durably.
    ``note`` is set when it lands but rides no completion — declared against the closed
    reference lease an open takeover names."""

    recorded: bool
    lease_id: str
    repo: str
    environment_id: str
    note: str | None = None


class LeaseGitCommitView(WireModel):
    """One commit a lease's worker declared: its newest declaration for this ``(environment_id, repo)``."""

    environment_id: str
    repo: str
    branch: str
    commit: str
    declared_at: str
