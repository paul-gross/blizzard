"""The hub liveness probe — ``GET /api/health``.

A dependency-free readiness signal: it answers whenever the hub is serving.
"""

from __future__ import annotations

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import APIRouter
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


@router.get("/health", response_model=HealthResponse)
@inject_sync
def health(flags: FromDishka[Flags]) -> HealthResponse:
    return HealthResponse(status="ok", service="blizzard-hub", version=__version__, overridden=list(flags.overridden))
