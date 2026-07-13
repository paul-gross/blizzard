"""Route routes — ``POST /api/routes`` (D-021/D-080).

The claim: acquisition is the birth of a complete route fact. The hub accepts
exactly one claim per chunk; a losing claim gets **409** (:class:`RouteClaimConflict`)
and the runner releases its bindings. A winning claim's response carries the first
node envelope (:class:`RouteClaimResponse`). 501 stub — the P6 runner/hub tracks
wire the single-claim CAS and the envelope build.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from blizzard.wire.route import RouteClaim, RouteClaimResponse

router = APIRouter(prefix="/api", tags=["routes"])

_NOT_IMPLEMENTED = "route claim lands in the P6 walking skeleton"


@router.post("/routes", response_model=RouteClaimResponse, status_code=status.HTTP_201_CREATED)
def claim_route(claim: RouteClaim) -> RouteClaimResponse:
    """Claim a chunk; 409 if already claimed, else the first node envelope (D-080)."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=_NOT_IMPLEMENTED)
