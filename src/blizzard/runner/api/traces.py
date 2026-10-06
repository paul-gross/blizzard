"""Runner trace operator routes: the status read and the replay, on the local operator lane.

Reached with local operator auth, like the pause brake; a worker's lease token is no credential here."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from blizzard.foundation.platform_tracing.signals import TelemetrySignal
from blizzard.foundation.store.utc import as_utc, iso_utc
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.tracing.replay import ReplayUnavailable, ReplayWindowRefused
from blizzard.wire.runner_traces import (
    HarnessSignalStatus,
    HarnessTelemetryStatus,
    ReceiverStatus,
    RunnerTraceReplayFailure,
    RunnerTraceReplayResponse,
    RunnerTraceStatusResponse,
    TraceReplayRequest,
)

router = APIRouter(prefix="/api/traces", tags=["traces"])


@router.get("/status", response_model=RunnerTraceStatusResponse)
def trace_status(request: Request) -> RunnerTraceStatusResponse:
    """Tracing on or off, the redacted endpoint, the cursor and its lag, the last export and the last error,
    and Claude Code's harness-telemetry plan with each signal's receiver counts."""
    read = RunnerWiring.of(request).trace_status().read()
    harness = read.harness_telemetry
    return RunnerTraceStatusResponse(
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
        receiver=(
            ReceiverStatus(accepted_spans=read.receiver.accepted, dropped_spans=read.receiver.dropped)
            if read.receiver
            else None
        ),
        replay_max_window_seconds=read.replay_max_window_seconds,
        harness_telemetry=(
            HarnessTelemetryStatus(
                **{
                    signal.value: HarnessSignalStatus(
                        outcome=harness.plan.outcome(signal),
                        accepted=harness.receivers[signal].accepted,
                        dropped=harness.receivers[signal].dropped,
                    )
                    for signal in TelemetrySignal
                }
            )
            if harness
            else None
        ),
    )


@router.post(
    "/replay",
    response_model=RunnerTraceReplayResponse,
    responses={status.HTTP_502_BAD_GATEWAY: {"model": RunnerTraceReplayFailure}},
)
def trace_replay(body: TraceReplayRequest, request: Request) -> RunnerTraceReplayResponse | JSONResponse:
    """Tell every lease closed in ``[since, until)`` again, inside the request, without moving the live cursor.
    A bad window is 422; a replay that must export while tracing is off, or any replay before the runner's first
    registration, is 409; and an exporter that refuses is 502 with the counts it accepted before."""
    try:
        result = (
            RunnerWiring.of(request).trace_replay().replay(as_utc(body.since), as_utc(body.until), dry_run=body.dry_run)
        )
    except ReplayWindowRefused as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ReplayUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if result.failed:
        failure = RunnerTraceReplayFailure(
            detail="the trace exporter did not accept a batch; the replay stopped",
            leases=result.leases,
            spans=result.spans,
            batches=result.batches,
        )
        return JSONResponse(status_code=status.HTTP_502_BAD_GATEWAY, content=failure.model_dump())
    return RunnerTraceReplayResponse(
        leases=result.leases, spans=result.spans, batches=result.batches, dry_run=result.dry_run
    )
