"""The operator's read of runner tracing: on or off, where it exports, the cursor and its lag, what last failed.

Contract: ``blizzard-product:/delivered/tracing/runner-spans/spec/emission.md`` §Operator surface, reporting the
same fields as the hub's. Everything here is a fact the sweep left in the store or the settings parsed at start
(``bzh:facts-not-status``), so it survives a restart and needs no reach into the sweep. The clock is injected;
no write is possible."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.clock import IClock
from blizzard.foundation.platform_tracing.signals import TelemetrySignal
from blizzard.foundation.roles import dto
from blizzard.foundation.trace_export.settings import TracingSettings, TracingState
from blizzard.runner.harness.harness_telemetry_plan import HarnessTelemetryPlan
from blizzard.runner.tracing.cursor import LeaseCursorKey
from blizzard.runner.tracing.receiver_limits import ReceiverCount, ReceiverCounter
from blizzard.runner.tracing.repository import IReadLeaseTraceCursor, LeaseTraceExportFailure
from blizzard.runner.tracing.sweep import FAILED_MESSAGE


@dto
@dataclass(frozen=True)
class HarnessTelemetryStatus:
    """The Claude Code binding's plan, and what each signal's receiver has taken in since start, in that
    signal's own unit (spans, data points, log records)."""

    plan: HarnessTelemetryPlan
    receivers: dict[TelemetrySignal, ReceiverCount]


@dto
@dataclass(frozen=True)
class LeaseTraceStatus:
    """``endpoint`` is the redacted origin; ``lag_seconds`` is the age of the oldest closed lease the cursor
    has not passed, ``None`` when nothing waits. ``last_error_at`` is when the newest failure began — the
    sweep records only the first failure after a success — and ``last_error_ongoing`` whether no export has
    succeeded since. ``receiver`` tallies worker spans since start, ``None`` where none is wired."""

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
    receiver: ReceiverCount | None = None
    replay_max_window_seconds: int | None = None
    harness_telemetry: HarnessTelemetryStatus | None = None


class LeaseTraceStatusReader:
    def __init__(
        self,
        *,
        settings: TracingSettings,
        leases: IReadLeaseTraceCursor,
        clock: IClock,
        receiver: ReceiverCounter | None = None,
        replay_max_window: int | None = None,
        harness_telemetry: HarnessTelemetryPlan | None = None,
        claude_trace_receiver: ReceiverCounter | None = None,
        metric_receiver: ReceiverCounter | None = None,
        log_receiver: ReceiverCounter | None = None,
    ) -> None:
        self._harness_telemetry = harness_telemetry
        self._signal_receivers = {
            TelemetrySignal.TRACES: claude_trace_receiver,
            TelemetrySignal.METRICS: metric_receiver,
            TelemetrySignal.LOGS: log_receiver,
        }
        self._replay_max_window = replay_max_window
        self._settings = settings
        self._receiver = receiver
        self._leases = leases
        self._clock = clock

    def read(self) -> LeaseTraceStatus:
        cursor = self._leases.newest_trace_cursor()
        exported = self._leases.newest_export_cursor()
        failure = self._leases.newest_export_failure()
        ongoing = export_failure_ongoing(failure, self._leases.newest_trace_latch() if failure is not None else None)
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
            receiver=self._receiver.count() if self._receiver else None,
            replay_max_window_seconds=self._replay_max_window,
            harness_telemetry=self._harness(),
        )

    def _harness(self) -> HarnessTelemetryStatus | None:
        if self._harness_telemetry is None:
            return None
        return HarnessTelemetryStatus(
            plan=self._harness_telemetry,
            receivers={
                signal: counter.count() if counter else ReceiverCount(0, 0)
                for signal, counter in self._signal_receivers.items()
            },
        )

    def _lag(self, position: LeaseCursorKey) -> float | None:
        now = self._clock.now()
        oldest = self._leases.oldest_unsent_lease(position, now)
        return cursor_lag(oldest.at if oldest is not None else None, now)


def export_failure_ongoing(failure: LeaseTraceExportFailure | None, newest_latch: str | None) -> bool:
    """Whether the newest export failure still stands: one was recorded and no export has
    succeeded since — the newest latch is still the failure's own."""
    return failure is not None and newest_latch == "trace-export-failed"


def cursor_lag(oldest_unsent_at: datetime | None, now: datetime) -> float | None:
    """The age in seconds of the oldest closed lease the cursor has not passed — never
    negative, and ``None`` when nothing waits."""
    if oldest_unsent_at is None:
        return None
    return max((now - oldest_unsent_at).total_seconds(), 0.0)
