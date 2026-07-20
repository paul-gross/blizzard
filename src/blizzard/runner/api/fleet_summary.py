"""The runner-local fleet-summary pass-through proxy — ``GET /api/fleet-summary``.

The runner machine panel's hub rail shows a "Fleet · read from hub API" counts strip —
four integers (ready / running / waiting / needs) giving the operator a fleet-level pulse
without leaving the panel (issue #76). The panel is served by the runner and the hub API
allows no cross-origin browser read, so the browser cannot fetch the counts from the hub
directly: this route **forwards** the read to the hub, exactly as the PM-items proxy does
(:mod:`blizzard.runner.api.pm_items`) — panel -> own runner -> hub, on ``config.hub_url``.

Read-only over its wiring (``bzh:controller-read-only``): it forwards to the hub URL the
``host`` composition root resolved onto ``app.state.config``, carrying the same
``Authorization: Bearer`` credential as the reconciliation loop's own hub client
(``config.hub_token``) — one credential path for every runner->hub call, no header at all
when unenrolled. The forward targets the hub's fleet-router counterpart
(``/api/fleet/summary``), where the runner bearer token is confined; the board has no
anonymous counterpart because its own card list already carries every status.

Severable like PM-items: a transport failure to the hub is a ``502`` and the hub's own
status passes through verbatim, so the panel degrades its strip (dimmed / "last known")
on a distinct error rather than showing empty counts, and the hub-free local rails stay
unaffected. Counts are never stored on the path — a fresh fold each call.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.logging import get_logger
from blizzard.runner.config import RunnerConfig
from blizzard.wire.fleet import FleetSummaryView

router = APIRouter(prefix="/api", tags=["runner"])

_log = get_logger("blizzard.runner.api.fleet_summary")
_HUB_TIMEOUT = 15.0


@router.get("/fleet-summary", response_model=FleetSummaryView)
def get_fleet_summary(request: Request) -> FleetSummaryView:
    """Forward the fleet-summary read to the hub — the layered pass-through."""
    config: RunnerConfig | None = getattr(request.app.state, "config", None)
    if config is None or not config.hub_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runner not wired to a hub — start via `blizzard runner host`",
        )
    url = f"{config.hub_url.rstrip('/')}/api/fleet/summary"
    try:
        upstream = httpx.get(url, headers=config.auth_headers(), timeout=_HUB_TIMEOUT)
    except httpx.HTTPError as exc:
        _log.error("fleet-summary proxy could not reach the hub", error=str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"hub unreachable: {exc}") from exc
    if upstream.status_code != status.HTTP_200_OK:
        # Surface the hub's status verbatim so the panel degrades on the real reason.
        raise HTTPException(status_code=upstream.status_code, detail=_upstream_detail(upstream))
    return FleetSummaryView.model_validate(upstream.json())


def _upstream_detail(response: httpx.Response) -> str:
    """The hub's error detail, unwrapped from its JSON body when present."""
    try:
        payload = response.json()
    except ValueError:
        return response.text
    if isinstance(payload, dict) and "detail" in payload:
        return str(payload["detail"])
    return response.text
