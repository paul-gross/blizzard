"""Analytics operator-plane routes (blizzard#254 D7) — a forced re-derive over the same
per-segment replacement unit the standing sweep runs, for an operator who needs a
segment's events sooner than the next sweep tick, or a version bump re-derived now.
Gated on :data:`~blizzard.auth_core.ANALYTICS_ADMIN`, since this mutates, unlike the
read-only :data:`~blizzard.auth_core.TRANSCRIPT_READ` grant. Operator-plane, never
``/api/fleet/...`` — ``bzh:wire-change-extends-mock`` does not fire."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from blizzard.auth_core import ANALYTICS_ADMIN
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.wire.analytics import ReDeriveRequest, ReDeriveResponse

router = APIRouter(
    prefix="/api/analytics",
    tags=["analytics"],
    dependencies=[Depends(reject_runner_principal), Depends(require(ANALYTICS_ADMIN))],
)


@router.post("/re-derive", response_model=ReDeriveResponse)
def re_derive(request: ReDeriveRequest, services: Annotated[HubServices, Depends(get_services)]) -> ReDeriveResponse:
    """A segment scope forces that one segment regardless of its candidacy; a chunk or
    all scope derives up to ``limit`` of that scope's current candidates and reports how
    many remain, so the caller drives to convergence with repeated calls."""
    if request.segment_id is not None and request.chunk_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="segment_id and chunk_id are mutually exclusive"
        )
    service = services.event_derivation_service
    if request.segment_id is not None:
        service.derive_segment(request.segment_id)
        return ReDeriveResponse(derived=1, remaining=0)

    candidates = service.candidate_segment_ids(chunk_id=request.chunk_id)
    to_derive = candidates[: request.limit]
    for segment_id in to_derive:
        service.derive_segment(segment_id)
    return ReDeriveResponse(derived=len(to_derive), remaining=len(candidates) - len(to_derive))
