"""The hub half of the invariant checker on request (``bzh:invariant-checker``), run by hub administrators alone —
hub-level, under ``TENANT_ADMIN``. It audits every tenant's rows inside one read transaction."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from blizzard.foundation.invariants import InvariantsInProgress
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require_hub
from blizzard.hub.api.hub_level import Hub
from blizzard.hub.api.problem_rendering import problem_response
from blizzard.hub.api.problems import invariants_in_progress_problem
from blizzard.hub.auth.permissions import Permission
from blizzard.wire.invariants import InvariantsResponse, InvariantViolation

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(reject_runner_principal)])

_IN_PROGRESS: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {"description": "Another invariant check is running on this hub."}
}


@router.get(
    "/invariants",
    response_model=InvariantsResponse,
    responses=_IN_PROGRESS,
    dependencies=[Depends(require_hub(Permission.TENANT_ADMIN))],
)
def hub_invariants(services: Hub, request: Request) -> InvariantsResponse | JSONResponse:
    """Every breach of the hub store's invariants across every tenant, a per-tenant detail prefixed with its tenant;
    an empty list when every invariant holds."""
    try:
        violations = services.invariants.run()
    except InvariantsInProgress:
        return problem_response(invariants_in_progress_problem(instance=request.url.path))
    return InvariantsResponse(
        violations=[InvariantViolation(invariant=v.invariant, detail=v.detail) for v in violations]
    )
