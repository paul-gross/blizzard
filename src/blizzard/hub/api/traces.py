"""Fleet-trace operator-plane routes: the status read and the replay. Never ``/api/fleet/...``; a runner
principal is refused. Status gates on :attr:`~blizzard.auth_core.Permission.FLEET_VIEW` — it is redacted, so it
holds nothing beyond fleet state — and replay on :attr:`~blizzard.auth_core.Permission.ANALYTICS_ADMIN`."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import Permission
from blizzard.foundation.store.utc import as_utc, iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.observability.tracing.replay import ReplayUnavailable, ReplayWindowRefused
from blizzard.wire.traces import (
    TraceReplayFailure,
    TraceReplayRequest,
    TraceReplayResponse,
    TraceStatusResponse,
)

router = APIRouter(prefix="/api/traces", tags=["traces"], dependencies=[Depends(reject_runner_principal)])


@router.get("/status", response_model=TraceStatusResponse, dependencies=[Depends(require(Permission.FLEET_VIEW))])
def trace_status(services: Annotated[HubServices, Depends(get_services)]) -> TraceStatusResponse:
    """Tracing on or off, the redacted endpoint, the cursor and its lag, the last export and the last error."""
    read = services.trace_status.read()
    return TraceStatusResponse(
        state=read.state,
        endpoint=read.endpoint,
        rejected_setting=read.rejected_setting,
        rejected_value=read.rejected_value,
        cursor_at=iso_utc(read.cursor_at) if read.cursor_at else None,
        lag_seconds=read.lag_seconds,
        last_export_at=iso_utc(read.last_export_at) if read.last_export_at else None,
        last_export_span_count=read.last_export_span_count,
        last_error_at=iso_utc(read.last_error_at) if read.last_error_at else None,
        last_error_message=read.last_error_message,
        last_error_ongoing=read.last_error_ongoing,
        replay_max_window_seconds=read.replay_max_window_seconds,
    )


@router.post(
    "/replay",
    response_model=TraceReplayResponse,
    responses={status.HTTP_502_BAD_GATEWAY: {"model": TraceReplayFailure}},
    dependencies=[Depends(require(Permission.ANALYTICS_ADMIN))],
)
def trace_replay(
    request: TraceReplayRequest, services: Annotated[HubServices, Depends(get_services)]
) -> TraceReplayResponse | JSONResponse:
    """Tell every step closed and chunk finished in ``[since, until)`` again, without moving the live cursor.
    A bad window — inverted, too wide, or reaching past now — is 422, a replay that must export while tracing
    is off is 409, and an exporter that refuses is 502 with the counts it accepted before."""
    try:
        result = services.trace_replay.replay(as_utc(request.since), as_utc(request.until), dry_run=request.dry_run)
    except ReplayWindowRefused as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ReplayUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if result.failed:
        failure = TraceReplayFailure(
            detail="the trace exporter did not accept a batch; the replay stopped",
            steps=result.steps,
            spans=result.spans,
            batches=result.batches,
            chunks=result.chunks,
        )
        return JSONResponse(status_code=status.HTTP_502_BAD_GATEWAY, content=failure.model_dump())
    return TraceReplayResponse(
        steps=result.steps, spans=result.spans, batches=result.batches, dry_run=result.dry_run, chunks=result.chunks
    )
