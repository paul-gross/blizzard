"""The hub readiness probe — ``GET /api/ready``.

Where ``/api/health`` is a dependency-free *liveness* signal (the process is up),
readiness answers "is this daemon fit to serve?" — its store reachable and at the
expected schema revision. The edge holds a read-only view (``bzh:controller-read-only``):
it calls the composition-root-wired :class:`ReadinessService` and maps the domain
:class:`Readiness` to a response model — it never opens the store itself.

When no readiness service is wired (the store-free ``create_app`` used by the
OpenAPI export and unit tests), the probe reports ``ready=false`` with a clear
detail rather than pretending — the daemon's ``host`` path always wires one.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from blizzard.hub.domain.readiness import ReadinessService

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
