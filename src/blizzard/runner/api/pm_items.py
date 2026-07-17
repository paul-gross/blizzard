"""The runner-local PM-item pass-through proxy — ``GET /api/chunks/{id}/pm-items``.

A build worker reads its chunk's PM items — each pointer's issue body and comment thread —
through this proxy while it works the build node (``graphs/prompts/build.md``): the runner
**forwards** the read to the hub's pass-through route, and the hub calls the vendor with
its own credentials. The layering is the point: a worker never talks to the hub
or the PM system directly, and PM credentials never reach the runner. Contents are never
stored anywhere on the path — the pointer is the durable referent, the item is fetched
fresh each call.

Read-only over its wiring (``bzh:controller-read-only``): it forwards to the hub URL the
``host`` composition root resolved onto ``app.state.config``. ``httpx`` is used only to
reach the hub — the same outbound-only edge the reconciliation loop's hub client rides;
a transport failure to the hub is a ``502`` and the hub's own status (``404``
unknown chunk, ``503`` no work-source configured) passes through verbatim so the worker
sees the real reason. A per-pointer forge failure is not a status — the hub degrades it to
an ``error`` on that entry, so the worker still reads the pointers it did reach.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.logging import get_logger
from blizzard.runner.config import RunnerConfig
from blizzard.wire.chunk import PmItemsView

router = APIRouter(prefix="/api", tags=["runner"])

_log = get_logger("blizzard.runner.api.pm")
_HUB_TIMEOUT = 15.0


@router.get("/chunks/{chunk_id}/pm-items", response_model=PmItemsView)
def get_pm_items(chunk_id: str, request: Request) -> PmItemsView:
    """Forward a chunk's PM-items read to the hub — the layered pass-through."""
    config: RunnerConfig | None = getattr(request.app.state, "config", None)
    if config is None or not config.hub_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runner not wired to a hub — start via `blizzard runner host`",
        )
    url = f"{config.hub_url.rstrip('/')}/api/chunks/{chunk_id}/pm-items"
    try:
        upstream = httpx.get(url, timeout=_HUB_TIMEOUT)
    except httpx.HTTPError as exc:
        _log.error("pm-items proxy could not reach the hub", chunk_id=chunk_id, error=str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"hub unreachable: {exc}") from exc
    if upstream.status_code != status.HTTP_200_OK:
        # Surface the hub's status verbatim — 404 (unknown chunk) or 503 (no work-source
        # configured) — so the worker reads the real reason.
        raise HTTPException(status_code=upstream.status_code, detail=_upstream_detail(upstream))
    return PmItemsView.model_validate(upstream.json())


def _upstream_detail(response: httpx.Response) -> str:
    """The hub's error detail, unwrapped from its JSON body when present."""
    try:
        payload = response.json()
    except ValueError:
        return response.text
    if isinstance(payload, dict) and "detail" in payload:
        return str(payload["detail"])
    return response.text
