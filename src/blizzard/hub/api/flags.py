"""The hub's flags answer — ``GET /api/flags``, on the public plane beside ``GET /api/health``.

A read-only edge (``bzh:controller-read-only``): it publishes the :class:`Flags` the composition root resolved."""

from __future__ import annotations

from fastapi import APIRouter, Request

from blizzard.foundation.flags import Flags
from blizzard.wire.flags import FlagsResponse

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/flags")
def flags(request: Request) -> FlagsResponse:
    held: Flags = request.app.state.flags
    return FlagsResponse(flags=held.answer(), overridden=list(held.overridden))
