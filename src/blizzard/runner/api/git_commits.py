"""``blizzard runner artifact commit`` — ``POST /api/leases/{lease_id}/git-commits``.

A worker durably declares a ``git_commit`` artifact for a repo it touched, authorized by its inherited
lease token (presentation owned by ``lease_token.py``). ``404`` unknown lease, ``403``
bad token, ``400`` for a repo the lease does not hold, ``409`` once the outcome is buffered or the
standing refuses. A declaration against an open takeover's closed reference lease lands, riding no completion."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.runner.api.lease_scope import authorized_worker_lease
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.lifecycle.judgement.git_commit_declaration import (
    GitCommitDeclarationOnClosedLease,
    GitCommitDeclarationTooLate,
    GitCommitDeclarationUnknownRepo,
)
from blizzard.wire.git_commits import GitCommitDeclarationRequest, GitCommitDeclarationResponse

router = APIRouter(prefix="/api", tags=["runner"])


@router.post(
    "/leases/{lease_id}/git-commits",
    response_model=GitCommitDeclarationResponse,
    response_model_exclude_none=True,
    status_code=status.HTTP_200_OK,
)
def record_git_commit_declaration(
    lease_id: str, request_body: GitCommitDeclarationRequest, request: Request
) -> GitCommitDeclarationResponse:
    """Record a worker's explicit git-commit declaration for ``request_body.repo`` against
    its lease."""
    service = RunnerWiring.of(request).git_commits()
    worker = authorized_worker_lease(lease_id, request)
    try:
        declared = service.declare(
            worker,
            repo=request_body.repo,
            branch=request_body.branch,
            commit=request_body.commit,
            environment_id=request_body.environment_id,
        )
    except GitCommitDeclarationUnknownRepo as exc:
        # 400, not a silent accept: the detail names the repos the lease does hold, so the
        # worker can re-run the verb correctly while it is still alive to do so.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except (GitCommitDeclarationOnClosedLease, GitCommitDeclarationTooLate) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return GitCommitDeclarationResponse(
        recorded=True,
        lease_id=lease_id,
        repo=request_body.repo,
        environment_id=declared.environment_id,
        note=declared.note,
    )
