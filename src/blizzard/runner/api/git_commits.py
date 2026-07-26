"""``blizzard runner artifact commit`` — ``POST /api/leases/{lease_id}/git-commits``
(issue #143, Phase 3).

The CLI is a pure client of this one route: a worker durably declares a ``git_commit``
artifact for a repo it touched, authorized by the lease token it inherited at spawn
(``BLIZZARD_LEASE_TOKEN``, issue #113 Phase 1) — a structural sibling of
``runner/api/attachments.py``. Read-only over its wiring (``bzh:controller-read-only``):
the edge resolves the lease to an object through the read-only store already on
``app.state`` and delegates the write to the composition-root-wired
:class:`~blizzard.runner.domain.git_commit_declaration.GitCommitDeclarationService` — it
holds no write repository of its own. The token is presented as
``X-Blizzard-Lease-Token`` or a standard ``Authorization: Bearer`` header; either is
accepted, the dedicated header checked first.

``503`` when the store or the declaration service is unwired (the store-free app);
``404`` for an unknown or already-closed lease; ``403`` for a missing or mismatched
token; ``400`` for a repo (or environment) the lease does not hold, whose detail names
the ones it does; ``200`` on a recorded declaration.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.runner.api.lease_token import presented_lease_token
from blizzard.runner.domain.git_commit_declaration import (
    GitCommitDeclarationRejected,
    GitCommitDeclarationService,
    GitCommitDeclarationUnknownRepo,
)
from blizzard.runner.store.repository import IReadRunnerStore
from blizzard.wire.git_commits import GitCommitDeclarationRequest, GitCommitDeclarationResponse

router = APIRouter(prefix="/api", tags=["runner"])


def _service(request: Request) -> GitCommitDeclarationService:
    service: GitCommitDeclarationService | None = getattr(request.app.state, "git_commit_declarations", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="git-commit declaration service not wired — start via `blizzard runner host`",
        )
    return service


@router.post(
    "/leases/{lease_id}/git-commits", response_model=GitCommitDeclarationResponse, status_code=status.HTTP_200_OK
)
def record_git_commit_declaration(
    lease_id: str, request_body: GitCommitDeclarationRequest, request: Request
) -> GitCommitDeclarationResponse:
    """Record a worker's explicit git-commit declaration for ``request_body.repo`` against
    its lease."""
    service = _service(request)
    store: IReadRunnerStore | None = getattr(request.app.state, "runner_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runner store not wired — start via `blizzard runner host`",
        )
    lease = store.active_lease(lease_id)
    if lease is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no active lease {lease_id}")
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
        # 400, not a silent accept: the detail names the repos (or environments) the
        # lease does hold, so the worker can re-run the verb correctly while it is still
        # alive to do so.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return GitCommitDeclarationResponse(
        recorded=True, lease_id=lease_id, repo=request_body.repo, environment_id=environment_id
    )
