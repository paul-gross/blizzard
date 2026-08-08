"""The hub readiness probe — ``GET /api/ready``: is this daemon fit to serve, its store reachable and
at the expected schema revision?

A read-only edge (``bzh:controller-read-only``): it calls the wired :class:`ReadinessService` and maps
the domain :class:`Readiness` to a response model, never opening the store itself. With no readiness
service wired, the probe reports ``ready=false`` with a detail rather than pretending."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from blizzard.foundation.store.readiness import ReadinessService

router = APIRouter(prefix="/api", tags=["meta"])


class ReadinessResponse(BaseModel):
    """The wire shape of a readiness reading (openapi-ts consumes this)."""

    ready: bool
    store_reachable: bool
    store_revision: str | None
    expected_revision: str | None
    detail: str


@router.get("/ready")
def ready(request: Request) -> ReadinessResponse:
    service: ReadinessService | None = getattr(request.app.state, "readiness", None)
    if service is None:
        return ReadinessResponse(
            ready=False,
            store_reachable=False,
            store_revision=None,
            expected_revision=None,
            detail="readiness service not wired (store-free app)",
        )
    r = service.evaluate()
    return ReadinessResponse(
        ready=r.ready,
        store_reachable=r.store_reachable,
        store_revision=r.store_revision,
        expected_revision=r.expected_revision,
        detail=r.detail,
    )
