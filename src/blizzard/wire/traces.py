"""Fleet-trace operator wire bodies — ``GET /api/traces/status`` and ``POST /api/traces/replay``."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ReceiverStatus(BaseModel):
    """The runner's span receiver since it started: worker spans accepted into its export pipeline, and
    spans refused — out of step, off allowlist, or over a cap."""

    accepted_spans: int
    dropped_spans: int


class TraceStatusResponse(BaseModel):
    """Whether fleet tracing runs and how it is doing. ``endpoint`` is the configured origin only; no exporter
    error text is carried. ``lag_seconds`` is the oldest unexported item's age, ``None`` when nothing waits.
    ``receiver`` is the runner's span-receiver tally, ``None`` on the hub. ``replay_max_window_seconds`` is the
    widest window one replay request may cover."""

    state: Literal["enabled", "disabled", "rejected"]
    endpoint: str | None
    rejected_setting: str | None
    rejected_value: str | None
    cursor_at: str | None
    lag_seconds: float | None
    last_export_at: str | None
    last_export_span_count: int | None
    last_error_at: str | None
    last_error_message: str | None
    last_error_ongoing: bool
    receiver: ReceiverStatus | None = None
    replay_max_window_seconds: int | None = None


class TraceReplayRequest(BaseModel):
    """The half-open window ``[since, until)`` to tell again — a ``trace-window-skipped`` event's own
    ``since``/``until`` pastes straight in. ``dry_run`` counts what would be told and exports nothing."""

    since: datetime
    until: datetime
    dry_run: bool = False


class TraceReplayResponse(BaseModel):
    """What a replay told, or with ``dry_run`` would have told; ``chunks`` counts chunk spans and markers."""

    steps: int
    spans: int
    batches: int
    dry_run: bool
    chunks: int = 0


class TraceReplayFailure(BaseModel):
    """The exporter refused or raised: ``steps``, ``spans`` and ``batches`` count what it accepted before the
    replay stopped. ``detail`` is fixed text, never the exporter's own error."""

    detail: str
    steps: int
    spans: int
    batches: int
    chunks: int = 0
