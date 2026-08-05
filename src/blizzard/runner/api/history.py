"""``blizzard runner chunk history`` — a worker's read of its own chunk's transition
history (issue #237).

Lease-scoped and token-authorized, then forwarded to the hub as the runner principal.
``503`` unwired, ``404`` unknown/closed lease, ``403`` bad token, ``502`` on a failed
forward; authorization resolves before the hub is consulted."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.logging import get_logger
from blizzard.runner.api.lease_scope import authorized_lease, upstream_detail
from blizzard.runner.config import RunnerConfig
from blizzard.wire.history import ChunkHistoryView, HistoryRowView, history_rows

router = APIRouter(prefix="/api", tags=["runner"])

_log = get_logger("blizzard.runner.api.history")
_HUB_TIMEOUT = 15.0


@router.get("/leases/{lease_id}/history", response_model=list[HistoryRowView])
def get_history(lease_id: str, request: Request) -> list[HistoryRowView]:
    """The worker's own chunk's timeline — transitions, migrations, and bounces merged
    oldest-first into one kind-discriminated read. Does not include the in-flight
    node-step this call is itself part of: a transition is recorded only once an
    attempt completes, so a worker must not read its own current step's absence as a
    gap in the history."""
    lease = authorized_lease(lease_id, request)
    config: RunnerConfig | None = getattr(request.app.state, "config", None)
    if config is None or not config.hub_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runner not wired to a hub — start via `blizzard runner host`",
        )
    url = f"{config.hub_url.rstrip('/')}/api/fleet/chunks/{lease.chunk_id}"
    try:
        upstream = httpx.get(url, headers=config.auth_headers(), timeout=_HUB_TIMEOUT)
    except httpx.HTTPError as exc:
        _log.error("history proxy could not reach the hub", chunk_id=lease.chunk_id, error=str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"hub unreachable: {exc}") from exc
    if upstream.status_code != status.HTTP_200_OK:
        raise HTTPException(status_code=upstream.status_code, detail=upstream_detail(upstream))
    detail = ChunkHistoryView.model_validate(upstream.json())
    return history_rows(detail)
