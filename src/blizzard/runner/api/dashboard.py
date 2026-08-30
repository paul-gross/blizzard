"""The runner-local composed dashboard read — ``GET /api/dashboard`` (issue #311).

Folds the panel's seven status polls (``/runner``, ``/environments``, ``/asks?open=true``,
``/escalations``, ``/takeovers``, ``/facts``, ``/fleet-summary``) into one response, each
section built by the same extracted view-builder its own individual route calls
(``canon:one-owner`` — one place owns each section's wire shape). The six local sections
are read-only over their wiring (``bzh:controller-read-only``) and always populate; only
``fleet_summary`` is a hub pass-through, so it alone degrades to ``None`` rather than
failing the whole read, on a hub outage or an unwired runner."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.exceptions import HTTPException

from blizzard.runner.api.asks import _ask_list
from blizzard.runner.api.control import _runner_status_view
from blizzard.runner.api.environments import _environment_list
from blizzard.runner.api.escalations import _escalation_list
from blizzard.runner.api.facts import DEFAULT_FACT_LIMIT, _fact_list
from blizzard.runner.api.fleet_summary import _fleet_summary
from blizzard.runner.api.hub_proxy import HubProxy
from blizzard.runner.api.takeovers import _open_takeover_list
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.domain.asks import IReadAskRepository
from blizzard.wire.fleet import FleetSummaryView
from blizzard.wire.runner_status import DashboardView

router = APIRouter(prefix="/api", tags=["runner"])

#: A slow hub must not stall the six local sections behind it, so this route's own
#: outbound call fails fast rather than riding the ``HubProxy`` module default (15s).
_DASHBOARD_HUB_TIMEOUT = 3.0


@router.get("/dashboard", response_model=DashboardView)
def get_dashboard(request: Request) -> DashboardView:
    """The panel's seven reads composed into one — the six local sections always
    populate; ``fleet_summary`` is ``None`` on a hub outage or an unwired runner."""
    wiring = RunnerWiring.of(request)
    service = wiring.status()
    asks: IReadAskRepository = wiring.stores().asks
    return DashboardView(
        runner=_runner_status_view(service),
        environments=_environment_list(service),
        asks=_ask_list(asks),
        escalations=_escalation_list(service),
        takeovers=_open_takeover_list(service),
        facts=_fact_list(service, DEFAULT_FACT_LIMIT),
        fleet_summary=_maybe_fleet_summary(request),
    )


def _maybe_fleet_summary(request: Request) -> FleetSummaryView | None:
    try:
        proxy = HubProxy.of(request, "dashboard")
    except HTTPException:
        # Unwired to a hub — the same shape an unenrolled runner already reports.
        return None
    try:
        # A hub outage here is tolerated degradation, not an operational failure — the six
        # local sections still stand, so this route's own unreachable-hub line logs below
        # the module default (issue #374).
        return _fleet_summary(proxy, timeout=_DASHBOARD_HUB_TIMEOUT, severity="warning")
    except HTTPException:
        # Hub unreachable, or answered with a non-200 — the local sections still stand.
        return None
