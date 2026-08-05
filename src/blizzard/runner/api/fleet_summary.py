"""The runner-local fleet-summary pass-through proxy — ``GET /api/fleet-summary``.

The hub API allows no cross-origin browser read, so this route forwards the read on
``config.hub_url`` (issue #76). Read-only over its wiring (``bzh:controller-read-only``),
carrying ``config.hub_token`` as a bearer — no header at all when unenrolled. Severable:
a transport failure is a ``502`` and the hub's own status passes through verbatim."""

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
