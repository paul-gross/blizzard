"""Runner-trace operator wire bodies — ``GET /api/traces/status`` and ``POST /api/traces/replay`` on the runner.

The status body and the replay request are the hub's own (:mod:`blizzard.wire.traces`); only the counts differ,
since the runner tells leases where the hub tells steps."""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.wire.traces import TraceReplayRequest, TraceStatusResponse

__all__ = ["RunnerTraceReplayFailure", "RunnerTraceReplayResponse", "TraceReplayRequest", "TraceStatusResponse"]


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
