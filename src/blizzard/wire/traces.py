"""Fleet-trace operator wire bodies — ``GET /api/traces/status`` and ``POST /api/traces/replay``."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class TraceStatusResponse(BaseModel):
    """Whether fleet tracing runs and how it is doing. ``endpoint`` is the configured origin only —
    scheme, host and port. ``rejected_setting``/``rejected_value`` name what a ``rejected`` state could not
    honor. ``lag_seconds`` is the age of the oldest closed step the cursor has not passed, ``None`` when
    nothing waits. ``last_error_at`` is when the newest failure began, and ``last_error_ongoing`` whether no
    export has succeeded since; no exporter error text is carried, only the event's fixed message."""

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


class TraceReplayRequest(BaseModel):
    """The half-open window ``[since, until)`` to tell again — a ``trace-window-skipped`` event's own
    ``since``/``until`` pastes straight in. ``dry_run`` counts what would be told and exports nothing."""

    since: datetime
    until: datetime
    dry_run: bool = False


class TraceReplayResponse(BaseModel):
    """What a replay told, or with ``dry_run`` would have told."""

    steps: int
    spans: int
    batches: int
    dry_run: bool


class TraceReplayFailure(BaseModel):
    """The exporter refused or raised: ``steps``, ``spans`` and ``batches`` count what it accepted before the
    replay stopped. ``detail`` is fixed text, never the exporter's own error."""

    detail: str
    steps: int
    spans: int
    batches: int
