"""``blizzard runner artifact commit`` — ``POST /api/leases/{lease_id}/git-commits`` (#143).

A worker durably declares a ``git_commit`` artifact for a repo it touched, authorized by
the lease token it inherited at spawn, presented as ``X-Blizzard-Lease-Token`` or a bearer
header (the dedicated one checked first). ``404`` unknown lease, ``403`` bad token, and
``400`` for a repo the lease does not hold."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.runner.api.lease_token import presented_lease_token
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.domain.git_commit_declaration import (
    GitCommitDeclarationRejected,
    GitCommitDeclarationUnknownRepo,
)
from blizzard.wire.git_commits import GitCommitDeclarationRequest, GitCommitDeclarationResponse

router = APIRouter(prefix="/api", tags=["runner"])


@router.post(
    "/leases/{lease_id}/git-commits", response_model=GitCommitDeclarationResponse, status_code=status.HTTP_200_OK
)
def record_git_commit_declaration(
    lease_id: str, request_body: GitCommitDeclarationRequest, request: Request
) -> GitCommitDeclarationResponse:
    """Record a worker's explicit git-commit declaration for ``request_body.repo`` against
    its lease."""
    service = RunnerWiring.of(request).git_commits()
    lease = RunnerWiring.of(request).worker_lease(lease_id)
    try:
        environment_id = service.declare(
            lease,
            presented_token=presented_lease_token(request),
            repo=request_body.repo,
            branch=request_body.branch,
            commit=request_body.commit,
            environment_id=request_body.environment_id,
        )
    except GitCommitDeclarationRejected as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except GitCommitDeclarationUnknownRepo as exc:
        # 400, not a silent accept: the detail names the repos the lease does hold, so the
        # worker can re-run the verb correctly while it is still alive to do so.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return GitCommitDeclarationResponse(
        recorded=True, lease_id=lease_id, repo=request_body.repo, environment_id=environment_id
    )
