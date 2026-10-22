"""The floor-clear endpoints — ``POST /api/host/subscriptions/{slug}/floor/clear`` on the host, and
``POST /api/runner/floor/clear`` on each runner.

A usage limit holds a subscription's floor until its reset passes or a sample shows it open; these let a person open
it sooner. A subscription's floor is shared by every runner spending it, so it is cleared by slug: a runner that
spends one refuses a clear of its own (``409``, naming the slug), and a slug the host does not declare is ``404``."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.throttle.floor import RunnerFloorClearRefused
from blizzard.runner.throttle.usage_limits import FloorClear
from blizzard.wire.base import WireModel

host_router = APIRouter(prefix="/api/host/subscriptions/{slug}", tags=["host"])
runner_router = APIRouter(prefix="/api/runner", tags=["runner"])


class FloorClearRequest(BaseModel):
    """Who is opening the floor — recorded on the clear."""

    by: str = "operator"


class FloorClearView(WireModel):
    """The clear just recorded."""

    #: The subscription whose floor was opened; ``None`` for a runner's own.
    slug: str | None
    cleared_at: str
    by: str


_HOST_RESPONSES: dict[int | str, dict[str, Any]] = {404: {"description": "The host declares no such subscription."}}
_RUNNER_RESPONSES: dict[int | str, dict[str, Any]] = {
    409: {"description": "The runner spends a subscription; the detail names its slug, the floor to clear instead."},
    503: {"description": "The runner's store is bound to no runner id."},
}


@host_router.post("/floor/clear", response_model=FloorClearView, responses=_HOST_RESPONSES)
def clear_subscription_floor(slug: str, request_body: FloorClearRequest, request: Request) -> FloorClearView:
    """Open a subscription's usage-limit floor now, for every runner spending it.

    A held start goes ahead on its next ask; a limit reported after the clear holds the floor again."""
    wiring = RunnerWiring.of(request)
    if all(declared.slug != slug for declared in wiring.subscriptions()):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no subscription {slug} on this host")
    return _view(slug, wiring.subscription_floor_clears().clear(slug, by=request_body.by))


@runner_router.post("/floor/clear", response_model=FloorClearView, responses=_RUNNER_RESPONSES)
def clear_runner_floor(request_body: FloorClearRequest, request: Request) -> FloorClearView:
    """Open this runner's own usage-limit floor now — only for a runner spending no subscription.

    A runner spending one is refused with ``409`` naming it, since its floor is that subscription's and opens for
    every runner spending it."""
    wiring = RunnerWiring.of(request)
    wiring.require_bound()
    try:
        clear = wiring.runner_floor_clears().clear(by=request_body.by)
    except RunnerFloorClearRefused as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"slug": exc.slug, "message": str(exc)},
        ) from exc
    return _view(None, clear)


def _view(slug: str | None, clear: FloorClear) -> FloorClearView:
    return FloorClearView(slug=slug, cleared_at=iso_utc(clear.cleared_at), by=clear.by)
