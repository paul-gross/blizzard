"""Fleet-wide reads spanning every chunk — currently just spend-since (issue #60).

``GET /api/spend`` sums every usage fact in the requested window into one fleet-wide
total, derived at read time (``bzh:facts-not-status``), never a stored column. The window
is half-open — ``since`` inclusive, optional ``until`` exclusive (issue #183)."""

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
from blizzard.hub.domain.work import UsageTotal
from blizzard.wire.fleet import FleetSpendView

router = APIRouter(prefix="/api", tags=["spend"], dependencies=[Depends(reject_runner_principal)])


def _parse_instant(value: str, *, field: str) -> datetime:
    try:
        return as_utc(datetime.fromisoformat(value))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field} {value!r} is not a valid ISO-8601 instant",
        ) from exc


@router.get("/spend", response_model=FleetSpendView, dependencies=[Depends(require(FLEET_VIEW))])
def fleet_spend(
    since: str, services: Annotated[HubServices, Depends(get_services)], until: str | None = None
) -> FleetSpendView:
    """The fleet's total usage/cost since ``since`` (an ISO-8601 instant) — summed
    over every usage fact recorded at or after it, across every chunk. An optional
    ``until`` bounds the window's other edge, exclusive."""
    cutoff = _parse_instant(since, field="since")
    upper = _parse_instant(until, field="until") if until is not None else None
    usage = UsageTotal.of(services.chunks.usage_since(cutoff, until=upper))
    return FleetSpendView(
        since=since,
        until=until,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_create_tokens=usage.cache_create_tokens,
        cost_usd=usage.cost_usd,
        cost_partial=usage.cost_partial,
    )
