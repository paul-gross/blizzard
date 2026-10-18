"""The hub liveness probe — ``GET /api/health``.

A dependency-free readiness signal the service tier, the board, and ``winter
service … --wait`` can poll. Deeper status (chunks, questions, runners) is derived
from facts and served by the fleet routers the backend builder fills in.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from blizzard import __version__
from blizzard.foundation.flags import Flags

router = APIRouter(prefix="/api", tags=["meta"])


class HealthResponse(BaseModel):
    """The wire shape of the liveness probe; ``overridden`` names the flags this hub's ``[flags]`` table changed."""

    status: str
    service: str
    version: str
    overridden: list[str]


@router.get("/health")
def health(request: Request) -> HealthResponse:
    flags: Flags = request.app.state.flags
    return HealthResponse(status="ok", service="blizzard-hub", version=__version__, overridden=list(flags.overridden))
