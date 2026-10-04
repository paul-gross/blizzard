"""Runner-trace operator wire bodies — ``GET /api/traces/status`` and ``POST /api/traces/replay`` on the runner.

The replay request is the hub's own (:mod:`blizzard.wire.traces`). The status body is the hub's plus the runner-only
harness-telemetry read; the hub's body is not widened. The replay counts differ, since the runner tells leases where
the hub tells steps."""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.foundation.harness_telemetry_outcome import HarnessTelemetryOutcome
from blizzard.wire.traces import ReceiverStatus, TraceReplayRequest, TraceStatusResponse

__all__ = [
    "HarnessSignalStatus",
    "HarnessTelemetryStatus",
    "ReceiverStatus",
    "RunnerTraceReplayFailure",
    "RunnerTraceReplayResponse",
    "RunnerTraceStatusResponse",
    "TraceReplayRequest",
    "TraceStatusResponse",
]


class HarnessSignalStatus(BaseModel):
    """One signal of Claude Code's telemetry: what the binding does with its exporter, and what the runner's
    receiver for it has accepted and dropped since start, in that signal's own unit."""

    outcome: HarnessTelemetryOutcome
    accepted: int
    dropped: int


class HarnessTelemetryStatus(BaseModel):
    traces: HarnessSignalStatus
    metrics: HarnessSignalStatus
    logs: HarnessSignalStatus


class RunnerTraceStatusResponse(TraceStatusResponse):
    """The shared status body, plus the runner's harness-telemetry read; ``None`` where the plan is not wired."""

    harness_telemetry: HarnessTelemetryStatus | None = None


class RunnerTraceReplayResponse(BaseModel):
    """What a replay told, or with ``dry_run`` would have told."""

    leases: int
    spans: int
    batches: int
    dry_run: bool


class RunnerTraceReplayFailure(BaseModel):
    """The exporter refused or raised: ``leases``, ``spans`` and ``batches`` count what it accepted before the
    replay stopped. ``detail`` is fixed text, never the exporter's own error."""

    detail: str
    leases: int
    spans: int
    batches: int
