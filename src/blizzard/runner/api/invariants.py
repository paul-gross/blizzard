"""The runner half of the invariant checker on request (``bzh:invariant-checker``), over every runner on the host,
inside one read transaction — the host's human lane."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from blizzard.foundation.invariants import InvariantsInProgress
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.wire.invariants import InvariantsResponse, InvariantViolation

router = APIRouter(prefix="/api/host/invariants", tags=["host"])

_IN_PROGRESS: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {
        "description": 'Another invariant check is running on this host: `{"code": "invariants_in_progress"}`.'
    }
}


@router.get("", response_model=InvariantsResponse, responses=_IN_PROGRESS)
def host_invariants(request: Request) -> InvariantsResponse | JSONResponse:
    """Every breach of the runner store's invariants across the host's runners, its live-process check included;
    an empty list when every invariant holds. Asked right after a restart, it is sound only after the runner's first
    completed pass."""
    try:
        violations = RunnerWiring.of(request).host_invariants().run()
    except InvariantsInProgress:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"code": "invariants_in_progress"})
    return InvariantsResponse(
        violations=[InvariantViolation(invariant=v.invariant, detail=v.detail) for v in violations]
    )
