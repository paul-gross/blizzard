"""The hub's flags answer — ``GET /api/flags``, on the public plane beside ``GET /api/health``.

A read-only edge (``bzh:controller-read-only``): it publishes the :class:`Flags` the composition root resolved."""

from __future__ import annotations

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import APIRouter

from blizzard.foundation.flags import Flags
from blizzard.wire.flags import FlagsResponse

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/flags", response_model=FlagsResponse)
@inject_sync
def flags(held: FromDishka[Flags]) -> FlagsResponse:
    return FlagsResponse(flags=held.answer(), overridden=list(held.overridden))
