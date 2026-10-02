"""The operator's read of runner tracing: on or off, where it exports, the cursor and its lag, what last failed.

Contract: ``blizzard-product:/plans/tracing/runner-spans/spec/emission.md`` §Operator surface, reporting the
same fields as the hub's. Everything here is a fact the sweep left in the store or the settings parsed at start
(``bzh:facts-not-status``), so it survives a restart and needs no reach into the sweep. The clock is injected;
no write is possible."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.clock import IClock
from blizzard.foundation.trace_export.settings import TracingSettings, TracingState
from blizzard.runner.domain.tracing.cursor import LeaseCursorKey
from blizzard.runner.domain.tracing.repository import IReadLeaseTraceCursor
from blizzard.runner.domain.tracing.sweep import FAILED_MESSAGE


@dataclass(frozen=True)
class LeaseTraceStatus:
    """``endpoint`` is the redacted origin; ``lag_seconds`` is the age of the oldest closed lease the cursor
    has not passed, ``None`` when nothing waits. ``last_error_at`` is when the newest failure began — the
    sweep records only the first failure after a success — and ``last_error_ongoing`` whether no export has
    succeeded since."""

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


class LeaseTraceStatusReader:
    def __init__(self, *, settings: TracingSettings, leases: IReadLeaseTraceCursor, clock: IClock) -> None:
        self._settings = settings
        self._leases = leases
        self._clock = clock

    def read(self) -> LeaseTraceStatus:
        cursor = self._leases.newest_trace_cursor()
        exported = self._leases.newest_export_cursor()
        failure = self._leases.newest_export_failure()
        ongoing = failure is not None and self._leases.newest_trace_latch() == "trace-export-failed"
        return LeaseTraceStatus(
            state=self._settings.state,
            endpoint=self._settings.endpoint,
            rejected_setting=self._settings.setting,
            rejected_value=self._settings.value,
            cursor_at=cursor.position.at if cursor else None,
            lag_seconds=self._lag(cursor.position) if cursor and self._settings.enabled() else None,
            last_export_at=exported.recorded_at if exported else None,
            last_export_span_count=exported.span_count if exported else None,
            last_error_at=failure.at if failure else None,
            last_error_message=FAILED_MESSAGE if failure else None,
            last_error_ongoing=ongoing,
        )

    def _lag(self, position: LeaseCursorKey) -> float | None:
        now = self._clock.now()
        oldest = self._leases.oldest_unsent_lease(position, now)
        return max((now - oldest.at).total_seconds(), 0.0) if oldest is not None else None
