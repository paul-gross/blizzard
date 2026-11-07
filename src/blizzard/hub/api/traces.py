"""Fleet-trace operator-plane routes: the status read and the replay. The trace export is hub-wide, shared by every
tenant, so status and replay alike gate on the hub-level
:attr:`~blizzard.hub.auth.permissions.Permission.EXPORT_ADMIN`."""

from __future__ import annotations

from functools import partial

from fastapi import APIRouter, Depends

from blizzard.foundation.store.utc import as_utc, iso_utc
from blizzard.hub.api import problems, problems_runners
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require_hub
from blizzard.hub.api.hub_level import Hub
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.auth.permissions import Permission
from blizzard.hub.domain.observability.tracing.replay import ReplayUnavailable, ReplayWindowRefused
from blizzard.wire.traces import (
    TraceReplayRequest,
    TraceReplayResponse,
    TraceStatusResponse,
)

router = APIRouter(prefix="/api/traces", tags=["traces"], dependencies=[Depends(reject_runner_principal)])


@router.get("/status", response_model=TraceStatusResponse, dependencies=[Depends(require_hub(Permission.EXPORT_ADMIN))])
def trace_status(services: Hub) -> TraceStatusResponse:
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
    dependencies=[Depends(require_hub(Permission.EXPORT_ADMIN))],
)
def trace_replay(request: TraceReplayRequest, services: Hub) -> TraceReplayResponse:
    """Tell every step closed and chunk finished in ``[since, until)`` again, without moving the live cursor.
    A bad window — inverted, too wide, or reaching past now — is 422, a replay that must export while tracing
    is off is 409, and an exporter that refuses is 502 with the counts it accepted before."""
    try:
        result = services.trace_replay.replay(as_utc(request.since), as_utc(request.until), dry_run=request.dry_run)
    except ReplayWindowRefused as exc:
        raise ProblemHTTPException(partial(problems_runners.trace_replay_window_refused_problem, exc)) from exc
    except ReplayUnavailable as exc:
        raise ProblemHTTPException(partial(problems_runners.trace_replay_unavailable_problem, exc)) from exc
    if result.failed:
        raise ProblemHTTPException(
            partial(problems.trace_replay_failed_problem, result.steps, result.spans, result.batches, result.chunks)
        )
    return TraceReplayResponse(
        steps=result.steps, spans=result.spans, batches=result.batches, dry_run=result.dry_run, chunks=result.chunks
    )
