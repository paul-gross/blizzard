"""Runner-trace operator wire bodies: the runner's trace status, with its harness-telemetry read, and its trace-replay
response and failure, alongside the shared shapes of :mod:`blizzard.wire.traces`."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel

from blizzard.foundation.harness_telemetry_outcome import HarnessTelemetryOutcome
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.traces import TraceStatusResponse
from blizzard.wire.vocabulary import OPEN

__all__ = [
    "HarnessSignalStatus",
    "HarnessTelemetryStatus",
    "RunnerTraceReplayFailure",
    "RunnerTraceReplayResponse",
    "RunnerTraceStatusResponse",
]


class HarnessSignalStatus(BaseModel):
    """One signal of the harness bindings' telemetry: what the enabled bindings together do with its exporter, and
    what the runner's receiver for it has accepted and dropped since start, in that signal's own unit."""

    outcome: Annotated[HarnessTelemetryOutcome | Unrecognized, OPEN]
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
