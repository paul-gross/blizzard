"""The host clock routes, mounted only on a host running the fenced controlled clock."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse

from blizzard.foundation.clock import ClockBackwards, ClockMove
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.wire.clock import ClockMoveRequest, ClockMoveResponse, ClockReading

host_clock_router = APIRouter(prefix="/api/host/clock", tags=["host"], include_in_schema=False)


@host_clock_router.get("", response_model=ClockReading)
def read_clock(request: Request) -> ClockReading:
    return ClockReading(now=iso_utc(RunnerWiring.of(request).controlled_clock().now()))


@host_clock_router.post("", response_model=ClockMoveResponse)
def move_clock(body: ClockMoveRequest, request: Request) -> ClockMoveResponse | JSONResponse:
    clock = RunnerWiring.of(request).controlled_clock()
    try:
        if body.to is not None:
            move: ClockMove = clock.set(body.to)
        else:
            assert body.advance_seconds is not None
            move = clock.advance(timedelta(seconds=body.advance_seconds))
    except ClockBackwards:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"code": "clock_backwards"})
    except (ValueError, OverflowError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return ClockMoveResponse(previous=iso_utc(move.previous), now=iso_utc(move.now))
