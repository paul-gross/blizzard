"""The runner-local fleet-summary pass-through proxy — ``GET /api/fleet-summary``.

The hub API allows no cross-origin browser read, so this route forwards the read on
``config.hub_url`` (issue #76). Read-only over its wiring (``bzh:controller-read-only``),
carrying ``config.hub_token`` as a bearer — no header at all when unenrolled."""

from __future__ import annotations

from fastapi import APIRouter, Request

from blizzard.runner.api.hub_proxy import HubProxy
from blizzard.wire.fleet import FleetSummaryView

router = APIRouter(prefix="/api", tags=["runner"])


@router.get("/fleet-summary", response_model=FleetSummaryView)
def get_fleet_summary(request: Request) -> FleetSummaryView:
    """Forward the fleet-summary read to the hub — the layered pass-through."""
    return _fleet_summary(HubProxy.of(request, "fleet-summary"))


def _fleet_summary(proxy: HubProxy, *, timeout: float | None = None) -> FleetSummaryView:
    upstream = proxy.get("/api/fleet/summary", timeout=timeout)
    return FleetSummaryView.model_validate(upstream.json())
