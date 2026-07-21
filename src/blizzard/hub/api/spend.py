"""Fleet-wide reads spanning every chunk — currently just spend-since (issue #60).

``GET /api/spend?since=<iso8601>`` sums every usage fact with ``recorded_at >=
since`` into one fleet-wide total; derived at read time (``bzh:facts-not-status``),
never a stored column. The caller picks the window — the board's "spend today" figure
passes local start-of-day, but the read itself stays general.

An anonymous **operator** verb, like every other read on this router
(``dependencies=[Depends(reject_runner_principal)]`` — issue #87): it lived at
``GET /api/fleet/spend`` until this phase, which collides with the new
runner-authenticated ``/api/fleet/*`` prefix (:mod:`blizzard.hub.api.fleet`), so it moved
here to free that namespace.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from blizzard.auth_core import FLEET_VIEW
from blizzard.foundation.store.utc import as_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.work import derive_fleet_usage
from blizzard.wire.fleet import FleetSpendView

router = APIRouter(prefix="/api", tags=["spend"], dependencies=[Depends(reject_runner_principal)])


@router.get("/spend", response_model=FleetSpendView, dependencies=[Depends(require(FLEET_VIEW))])
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
