"""The hub half of the invariant checker on request (``bzh:invariant-checker``), run by hub administrators alone —
hub-level, under ``TENANT_ADMIN``. It audits every tenant's rows inside one read transaction."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import Permission
from blizzard.foundation.invariants import InvariantsInProgress
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require_hub
from blizzard.hub.api.hub_level import Hub
from blizzard.wire.invariants import InvariantsResponse, InvariantViolation

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(reject_runner_principal)])

_IN_PROGRESS: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {
        "description": 'Another invariant check is running on this hub: `{"code": "invariants_in_progress"}`.'
    }
}


@router.get(
    "/invariants",
    response_model=InvariantsResponse,
    responses=_IN_PROGRESS,
    dependencies=[Depends(require_hub(Permission.TENANT_ADMIN))],
)
def hub_invariants(services: Hub) -> InvariantsResponse | JSONResponse:
    """Every breach of the hub store's invariants across every tenant, a per-tenant detail prefixed with its tenant;
    an empty list when every invariant holds."""
    try:
        violations = services.invariants.run()
    except InvariantsInProgress:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"code": "invariants_in_progress"})
    return InvariantsResponse(
        violations=[InvariantViolation(invariant=v.invariant, detail=v.detail) for v in violations]
    )
