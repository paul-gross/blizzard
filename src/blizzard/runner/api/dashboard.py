"""The runner-local composed dashboard read — ``GET /runners/{runner_id}/api/dashboard``.

Composes the panel's status reads into one response, each section built by its own
route's view-builder (``canon:one-owner``). The local sections are read-only over their
wiring (``bzh:controller-read-only``) and always populate; only ``fleet_summary`` is a hub
pass-through, so it alone degrades to ``None`` on a hub outage or an unwired runner."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.exceptions import HTTPException

from blizzard.runner.api.asks import ask_list
from blizzard.runner.api.control import runner_status_view
from blizzard.runner.api.escalations import escalation_list
from blizzard.runner.api.facts import DEFAULT_FACT_LIMIT, fact_list
from blizzard.runner.api.fleet_summary import fleet_summary
from blizzard.runner.api.harness_health import harness_health_list
from blizzard.runner.api.hub_proxy import HubProxy
from blizzard.runner.api.takeovers import open_takeover_list
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.leases.asks import IReadAskRepository
from blizzard.wire.fleet import FleetSummaryView
from blizzard.wire.runner_status import DashboardView

router = APIRouter(prefix="/api", tags=["runner"])

#: A slow hub must not stall the local sections, so the hub call carries its own short
#: whole-forward budget (see ``HubProxy.forward``).
DASHBOARD_HUB_TIMEOUT = 3.0


@router.get("/dashboard", response_model=DashboardView)
def get_dashboard(request: Request) -> DashboardView:
    """The panel's seven reads composed into one — the six local sections always
    populate; ``fleet_summary`` is ``None`` on a hub outage or an unwired runner."""
    wiring = RunnerWiring.of(request)
    service = wiring.status()
    stores = wiring.read_stores()
    asks: IReadAskRepository = stores.asks
    return DashboardView(
        runner=runner_status_view(service),
        asks=ask_list(asks, stores.environments),
        escalations=escalation_list(service, stores.environments),
        takeovers=open_takeover_list(service),
        facts=fact_list(service, stores.environments, DEFAULT_FACT_LIMIT),
        harness_health=harness_health_list(wiring.harnesses(), wiring.harness_health()),
        fleet_summary=_maybe_fleet_summary(request),
    )


def _maybe_fleet_summary(request: Request) -> FleetSummaryView | None:
    try:
        proxy = HubProxy.of(request, "dashboard")
    except HTTPException:
        # Unwired to a hub — the same shape an unenrolled runner already reports.
        return None
    try:
        # A hub outage is tolerated degradation, so its log line drops below the default severity.
        return fleet_summary(proxy, timeout=DASHBOARD_HUB_TIMEOUT, severity="warning")
    except HTTPException:
        # Hub unreachable, or answered with a non-200 — the local sections still stand.
        return None
