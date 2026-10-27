"""Passes on request: one tick of a runner, or one pass of the host, answered once it has finished. Each handler is
sync, so the pass runs off the event loop and every effect it has lands before the answer. Both serve a timed host
too, and both are on the human lane."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.loop.step_passes import PassResult, TickInProgress
from blizzard.wire.tick_passes import TickPassResponse

router = APIRouter(prefix="/api/ticks", tags=["ticks"])
host_router = APIRouter(prefix="/api/host/ticks", tags=["host"])

_IN_PROGRESS: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {
        "description": 'A pass of the same lock is in progress, timed or requested: `{"code": "tick_in_progress"}`.'
    }
}


def _response(result: PassResult) -> TickPassResponse:
    return TickPassResponse(
        started_at=iso_utc(result.started_at),
        finished_at=iso_utc(result.finished_at),
        outcome=result.outcome,
        error=result.error,
    )


def _in_progress() -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"code": "tick_in_progress"})


@router.post("", response_model=TickPassResponse, responses=_IN_PROGRESS)
def run_tick(request: Request) -> TickPassResponse | JSONResponse:
    """One tick of the runner — every phase a timed pass runs, then one trace sweep when tracing is on: ``failed``
    with the message of the fault a pass met."""
    try:
        return _response(RunnerWiring.of(request).stepper().request())
    except TickInProgress:
        return _in_progress()


@host_router.post("", response_model=TickPassResponse, responses=_IN_PROGRESS)
def run_host_tick(request: Request) -> TickPassResponse | JSONResponse:
    """One pass of the host's own work: registration, credential renewal, usage sampling, lane drain."""
    try:
        return _response(RunnerWiring.of(request).host_stepper().request())
    except TickInProgress:
        return _in_progress()
