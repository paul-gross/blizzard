"""The runner liveness probe — ``GET /api/health``.

A dependency-free readiness signal, answerable with no store wired. It also names the host's hub-minted id, so a
browser holding several candidate origins can tell which one answers as this host; the hub's own origin alone may read
the answer cross-origin."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from blizzard import __version__
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.wire.base import WireModel

router = APIRouter(prefix="/api", tags=["meta"])


class HealthResponse(WireModel):
    """The wire shape of a liveness answer."""

    status: str
    service: str
    version: str
    #: The hub-minted host id, ``None`` until the host's first answered report.
    host_id: str | None


@router.get("/health")
def health(request: Request, response: Response) -> HealthResponse:
    wiring = RunnerWiring.of(request)
    origin = request.headers.get("origin")
    if wiring.hub_origin().admits(origin):
        response.headers["Access-Control-Allow-Origin"] = str(origin)
        response.headers["Vary"] = "Origin"
    identity = wiring.host_identity().current()
    return HealthResponse(
        status="ok",
        service="blizzard-runner",
        version=__version__,
        host_id=identity.host_id if identity is not None else None,
    )
