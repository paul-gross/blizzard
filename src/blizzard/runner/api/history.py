"""``blizzard runner chunk history`` — a worker's read of its own chunk's transition
history (issue #237).

A retrospective node closing out a chunk cannot see the chunk's own journey today:
which nodes bounced, how many rounds a loop took, what choice routed each transition.
The hub already derives this timeline for the board (``hub.api.chunks._history_views``,
served on ``ChunkDetail``); what was missing is the worker-facing read path — a worker
holds no hub credential, and nothing proxied this view to it the way the envelope's
artifacts are proxied (``artifacts.py``).

Layered exactly like the artifacts proxy: lease-scoped and token-authorized via
:func:`~blizzard.runner.api.lease_scope.authorized_lease`, then forwarded to the hub's
runner-authenticated chunk-detail route (``GET /api/fleet/chunks/{id}``, the same one
:mod:`blizzard.runner.api.chunk_detail` reads) as the runner principal
(``config.auth_headers()``). The full ``ChunkDetail`` payload is validated down to the
internal :class:`~blizzard.wire.history.ChunkHistoryView` projection and flattened by
:func:`~blizzard.wire.history.history_rows` — no new hub-side model or route, and no
runner-store persistence; the read is live each call.

Status map mirrors the artifacts proxy: ``503`` when the store or the hub wiring is
absent, ``404`` for an unknown/closed lease, ``403`` for a missing/mismatched token, and
a ``502`` (or the hub's own status verbatim) when the forward fails. Authorization is
resolved before the hub is consulted.
"""

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
