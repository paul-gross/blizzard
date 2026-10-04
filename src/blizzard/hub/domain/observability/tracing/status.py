"""The operator's read of fleet tracing: on or off, where it exports, the cursor and its lag, what last failed.

Contract: ``blizzard-product:/delivered/tracing/fleet-spans/spec/emission.md`` §Operator surface. Everything
here is a fact the sweep left in the store or the settings parsed at start (``bzh:facts-not-status``), so it
survives a restart and needs no reach into the sweep. The clock is injected; no write is possible."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import dto
from blizzard.foundation.trace_export.settings import TracingSettings, TracingState
from blizzard.hub.domain.observability.lane_failure import failure_ongoing
from blizzard.hub.domain.observability.tracing.cursor import CursorKey
from blizzard.hub.domain.observability.tracing.repository import IReadTraceStatus, IReadTraceSteps
from blizzard.hub.domain.observability.tracing.window import oldest_unsent


@dto
@dataclass(frozen=True)
class TraceStatus:
    """``endpoint`` is the redacted origin; ``lag_seconds`` is the age of the oldest closed step or finished
    chunk the cursor has not passed, ``None`` when nothing waits. ``last_error_at`` is when the newest failure
    began — the sweep records only the first failure after a success — and ``last_error_ongoing`` whether no
    export has succeeded since."""

    state: TracingState
    endpoint: str | None
    rejected_setting: str | None
    rejected_value: str | None
    cursor_at: datetime | None
    lag_seconds: float | None
    last_export_at: datetime | None
    last_export_span_count: int | None
    last_error_at: datetime | None
    last_error_message: str | None
    last_error_ongoing: bool
    replay_max_window_seconds: int


class TraceStatusReader:
    def __init__(
        self,
        *,
        settings: TracingSettings,
        status: IReadTraceStatus,
        steps: IReadTraceSteps,
        clock: IClock,
        replay_max_window: int,
    ) -> None:
        self._settings = settings
        self._replay_max_window = replay_max_window
        self._status = status
        self._steps = steps
        self._clock = clock

    def read(self) -> TraceStatus:
        cursor = self._steps.newest_cursor()
        exported = self._status.newest_export_cursor()
        failure = self._status.newest_export_failure()
        ongoing = failure_ongoing(failure, self._steps.newest_export_latch(), failed_kind="trace-export-failed")
        return TraceStatus(
            state=self._settings.state,
            endpoint=self._settings.endpoint,
            rejected_setting=self._settings.setting,
            rejected_value=self._settings.value,
            cursor_at=cursor.position.at if cursor else None,
            lag_seconds=self._lag(cursor.position) if cursor and self._settings.enabled() else None,
            last_export_at=exported.recorded_at if exported else None,
            last_export_span_count=exported.span_count if exported else None,
            last_error_at=failure.at if failure else None,
            last_error_message=failure.message if failure else None,
            last_error_ongoing=ongoing,
            replay_max_window_seconds=self._replay_max_window,
        )

    def _lag(self, position: CursorKey) -> float | None:
        now = self._clock.now()
        oldest = oldest_unsent(self._steps, position, now)
        return max((now - oldest.key.at).total_seconds(), 0.0) if oldest is not None else None
