"""``blizzard runner takeover`` — ``POST``/``PATCH /chunks/{id}/takeovers`` (issue #52).

``POST`` opens a takeover and returns the adapter-composed interactive command plus its
workdir — the daemon never touches a TTY; ``PATCH`` marks it ended. ``GET /takeovers``
(issue #51) lists every one still open, the stranded-takeover recovery surface."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.domain.takeover import (
    ChunkNotTakeable,
    LiveWorkerConflict,
    SubmissionPending,
    TakeoverEndedElsewhere,
)
from blizzard.wire.runner_status import OpenTakeoverListResponse
from blizzard.wire.runner_status import OpenTakeoverView as OpenTakeoverViewWire
from blizzard.wire.takeover import TakeoverEndResponse, TakeoverOpenResponse, TakeoverRequest

router = APIRouter(prefix="/api", tags=["runner"])


@router.post("/chunks/{chunk_id}/takeovers", response_model=TakeoverOpenResponse, status_code=status.HTTP_201_CREATED)
def open_takeover(chunk_id: str, request_body: TakeoverRequest, request: Request) -> TakeoverOpenResponse:
    """Open a takeover over a parked chunk with no running attempt (``409`` otherwise).

    ``force`` supersedes a live worker attempt instead of refusing, consuming no retry
    and recording no escalation."""
    service = RunnerWiring.of(request).takeover()
    try:
        opened = service.open(chunk_id, force=request_body.force)
    except (ChunkNotTakeable, LiveWorkerConflict, SubmissionPending) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return TakeoverOpenResponse(
        takeover_id=opened.takeover_id, command=opened.command, workdir=opened.workdir, env=opened.env
    )


@router.patch("/chunks/{chunk_id}/takeovers/{takeover_id}", response_model=TakeoverEndResponse)
def end_takeover(chunk_id: str, takeover_id: str, request: Request) -> TakeoverEndResponse:
    """Mark a takeover ended — the CLI calls this once its exec'd interactive child exits."""
    service = RunnerWiring.of(request).takeover()
    try:
        service.close(chunk_id, takeover_id)
    except TakeoverEndedElsewhere as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return TakeoverEndResponse(takeover_id=takeover_id, ended=True)


@router.get("/takeovers", response_model=OpenTakeoverListResponse)
def list_open_takeovers(request: Request) -> OpenTakeoverListResponse:
    """Every takeover still open — the recovery surface for a stranded one."""
    service = RunnerWiring.of(request).status()
    return OpenTakeoverListResponse(
        items=[
            OpenTakeoverViewWire(chunk_id=t.chunk_id, takeover_id=t.takeover_id, held_since=iso_utc(t.held_since))
            for t in service.open_takeovers()
        ]
    )
