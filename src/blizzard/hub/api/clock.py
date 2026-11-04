"""Hidden controls for a hosted controlled clock."""

import asyncio
from datetime import timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from blizzard.foundation.clock import ClockBackwards, ControlledClock
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require_hub
from blizzard.hub.auth.permissions import Permission
from blizzard.wire.clock import ClockMoveRequest, ClockMoveResponse, ClockReading


def clock_router(clock: ControlledClock, lock: asyncio.Lock) -> APIRouter:
    router = APIRouter(
        prefix="/api/admin/clock",
        dependencies=[Depends(reject_runner_principal), Depends(require_hub(Permission.TENANT_ADMIN))],
        include_in_schema=False,
    )

    @router.get("")
    async def read() -> ClockReading:
        return ClockReading(now=iso_utc(clock.now()))

    @router.post("", response_model=None)
    async def move(body: ClockMoveRequest) -> ClockMoveResponse | JSONResponse:
        async with lock:
            try:
                moved = (
                    clock.set(body.to)
                    if body.to is not None
                    else clock.advance(timedelta(seconds=body.advance_seconds or 0))
                )
            except ClockBackwards:
                return JSONResponse(status_code=409, content={"code": "clock_backwards"})
            except (OverflowError, ValueError):
                return JSONResponse(status_code=422, content={"code": "invalid_clock_move"})
            return ClockMoveResponse(previous=iso_utc(moved.previous), now=iso_utc(moved.now))

    return router
