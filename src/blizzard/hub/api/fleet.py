"""Fleet-wide reads spanning every chunk — currently just spend-since (issue #60).

``GET /api/fleet/spend?since=<iso8601>`` sums every usage fact with ``recorded_at >=
since`` into one fleet-wide total; derived at read time (``bzh:facts-not-status``),
never a stored column. The caller picks the window — the board's "spend today" figure
passes local start-of-day, but the read itself stays general.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from blizzard.foundation.store.utc import as_utc
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.work import derive_fleet_usage
from blizzard.wire.fleet import FleetSpendView

router = APIRouter(prefix="/api", tags=["fleet"])


@router.get("/fleet/spend", response_model=FleetSpendView)
def fleet_spend(since: str, services: Annotated[HubServices, Depends(get_services)]) -> FleetSpendView:
    """The fleet's total usage/cost since ``since`` (an ISO-8601 instant) — summed
    over every usage fact recorded at or after it, across every chunk."""
    try:
        cutoff = as_utc(datetime.fromisoformat(since))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"since {since!r} is not a valid ISO-8601 instant",
        ) from exc
    usage = derive_fleet_usage(services.chunks.usage_since(cutoff))
    return FleetSpendView(
        since=since,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_create_tokens=usage.cache_create_tokens,
        cost_usd=usage.cost_usd,
        cost_partial=usage.cost_partial,
    )
