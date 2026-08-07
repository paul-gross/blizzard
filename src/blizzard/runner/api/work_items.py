"""The runner-local work-item pass-through proxy — ``GET /api/chunks/{id}/work-items``.

The forward is the layering: a caller here never reaches the work source directly, and
work-source credentials never reach the runner. Nothing is stored on the path — the
pointer is the durable referent. Read-only over its wiring (``bzh:controller-read-only``):
a transport failure is a ``502`` and an upstream status passes through verbatim."""

from __future__ import annotations

from fastapi import APIRouter, Request

from blizzard.runner.api.hub_proxy import HubProxy
from blizzard.wire.chunk import WorkItemsView

router = APIRouter(prefix="/api", tags=["runner"])


@router.get("/chunks/{chunk_id}/work-items", response_model=WorkItemsView)
def get_work_items(chunk_id: str, request: Request) -> WorkItemsView:
    """Forward a chunk's work-items read to the hub — the layered pass-through."""
    upstream = HubProxy.of(request, "work-items").get(f"/api/fleet/chunks/{chunk_id}/work-items", chunk_id=chunk_id)
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
