"""Route routes — ``POST /api/routes``.

The claim: acquisition is the birth of a complete route fact. The hub accepts
exactly one claim per chunk; a losing claim gets **409** (:class:`RouteClaimConflict`)
and the runner releases its bindings. A claim from a runner the hub registry marks
paused is refused outright with **403** (:class:`RouteClaimPausedDenial`, issue #44)
— never entering the race. A winning claim's response carries the first
node envelope (:class:`RouteClaimResponse`). The controller resolves the chunk and
graph and delegates the single-claim CAS to
:class:`~blizzard.hub.domain.claim.ClaimService` (``bzh:controller-read-only``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.claim import ClaimConflict, ClaimDeniedPaused
from blizzard.wire.route import RouteClaim, RouteClaimConflict, RouteClaimPausedDenial, RouteClaimResponse

router = APIRouter(prefix="/api", tags=["routes"])


@router.post("/routes", response_model=RouteClaimResponse, status_code=status.HTTP_201_CREATED)
def claim_route(claim: RouteClaim, services: Annotated[HubServices, Depends(get_services)]) -> object:
    """Claim a chunk; 403 if the runner is paused at the hub, 409 if already claimed,
    else the first node envelope."""
    chunk = services.chunks.get(claim.chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {claim.chunk_id}")
    graph = services.graphs.get(chunk.graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="chunk's pinned graph is missing")
    try:
        result = services.claim.claim(
            chunk,
            graph,
            runner_id=claim.runner_id,
            workspace_id=claim.workspace_id,
            environment_ids=claim.environment_ids,
        )
    except ClaimDeniedPaused as exc:
        denial = RouteClaimPausedDenial(chunk_id=claim.chunk_id, runner_id=exc.runner_id)
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content=denial.model_dump())
    except ClaimConflict as exc:
        conflict = RouteClaimConflict(chunk_id=claim.chunk_id, held_by_runner_id=exc.held_by_runner_id)
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=conflict.model_dump())
    services.events.publish_chunk_changed(chunk.chunk_id, "running")
    services.events.publish_queue_changed()  # the claim removed the chunk from the ready queue
    return RouteClaimResponse(
        chunk_id=result.route.chunk_id,
        runner_id=result.route.runner_id,
        workspace_id=result.route.workspace_id,
        environment_ids=result.route.environment_ids,
        envelope=result.envelope,
    )
