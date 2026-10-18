"""The host's flags answer — ``GET /api/flags``, at the root beside ``GET /api/health``.

A read-only edge (``bzh:controller-read-only``): it publishes the :class:`Flags` the composition root resolved."""

from __future__ import annotations

from fastapi import APIRouter, Request

from blizzard.runner.api.wiring import RunnerWiring
from blizzard.wire.flags import FlagsResponse

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/flags")
def flags(request: Request) -> FlagsResponse:
    held = RunnerWiring.of(request).flags()
    return FlagsResponse(flags=held.answer(), overridden=list(held.overridden))
