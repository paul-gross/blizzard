"""The runner-local work-item pass-through proxy — ``GET /api/chunks/{id}/work-items``.

The forward is the layering: a caller here never reaches the work source directly, and
work-source credentials never reach the runner. Nothing is stored on the path — the
pointer is the durable referent. Read-only over its wiring (``bzh:controller-read-only``):
a transport failure is a ``502`` and an upstream status passes through verbatim."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.logging import get_logger
from blizzard.runner.config import RunnerConfig
from blizzard.wire.chunk import WorkItemsView

router = APIRouter(prefix="/api", tags=["runner"])

_log = get_logger("blizzard.runner.api.work_items")
_HUB_TIMEOUT = 15.0


@router.get("/chunks/{chunk_id}/work-items", response_model=WorkItemsView)
def get_work_items(chunk_id: str, request: Request) -> WorkItemsView:
    """Forward a chunk's work-items read to the hub — the layered pass-through."""
    config: RunnerConfig | None = getattr(request.app.state, "config", None)
    if config is None or not config.hub_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runner not wired to a hub — start via `blizzard runner host`",
        )
    url = f"{config.hub_url.rstrip('/')}/api/fleet/chunks/{chunk_id}/work-items"
    try:
        upstream = httpx.get(url, headers=config.auth_headers(), timeout=_HUB_TIMEOUT)
    except httpx.HTTPError as exc:
        _log.error("work-items proxy could not reach the hub", chunk_id=chunk_id, error=str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"hub unreachable: {exc}") from exc
    if upstream.status_code != status.HTTP_200_OK:
        # Surface the hub's status verbatim — 404 (unknown chunk) or 503 (no work-source
        # configured) — so the worker reads the real reason.
        raise HTTPException(status_code=upstream.status_code, detail=_upstream_detail(upstream))
    return WorkItemsView.model_validate(upstream.json())


# The runner's half of the issue-#55 alias, so both daemons present the same path
# surface. The forward is unconditionally to the canonical `/work-items`.
router.add_api_route(
    "/chunks/{chunk_id}/pm-items",
    get_work_items,
    methods=["GET"],
    response_model=WorkItemsView,
    deprecated=True,
    name="runner_get_pm_items_deprecated_alias",
    summary="Deprecated alias for GET /chunks/{chunk_id}/work-items",
    description=(
        "Deprecated since issue #55 — use `GET /chunks/{chunk_id}/work-items`, which this "
        "path aliases onto the identical handler and returns the identical view."
    ),
)


def _upstream_detail(response: httpx.Response) -> str:
    """The hub's error detail, unwrapped from its JSON body when present."""
    try:
        payload = response.json()
    except ValueError:
        return response.text
    if isinstance(payload, dict) and "detail" in payload:
        return str(payload["detail"])
    return response.text
