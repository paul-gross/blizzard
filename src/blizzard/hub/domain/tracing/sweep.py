"""The trace export sweep: tells closed node steps, in cursor order, to the configured exporter.

Contract: ``blizzard-product:/plans/tracing/fleet-spans/spec/emission.md`` §What a sweep does,
§The cursor and §Delivery semantics. Every collaborator is injected, so :meth:`TraceExportSweep.sweep`
is one complete, directly-callable pass (``bzh:steppable-loop``)."""

from __future__ import annotations

from datetime import datetime, timedelta

from blizzard.foundation.clock import IClock
from blizzard.foundation.event_log import EventLogKind
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.config import TracingConfig
from blizzard.hub.domain.event_log import EventLogService
from blizzard.hub.domain.tracing.assembly import assemble_step
from blizzard.hub.domain.tracing.cursor import (
    CursorJump,
    CursorKey,
    backoff_delay,
    first_pass_jump,
    lag_cap_jump,
)
from blizzard.hub.domain.tracing.export import ITraceExporter
from blizzard.hub.domain.tracing.repository import IWriteTraceCursor, TraceCursorRecord
from blizzard.hub.domain.tracing.spans import SpanRecord
from blizzard.hub.domain.tracing.window import read_window

_log = get_logger("blizzard.hub.trace_export")

_FAILED: EventLogKind = "trace-export-failed"
_RECOVERED: EventLogKind = "trace-export-recovered"
_SKIPPED: EventLogKind = "trace-window-skipped"


def _key_detail(key: CursorKey) -> dict[str, object]:
    return {"at": iso_utc(key.at), "chunk_id": key.chunk_id, "epoch": key.epoch, "decision_id": key.decision_id}


class TraceExportSweep:
    """Select, assemble, export, advance — the cursor moves only once the exporter accepts."""

    def __init__(
        self,
        *,
        steps: IWriteTraceCursor,
        exporter: ITraceExporter,
        events: EventLogService,
        clock: IClock,
        config: TracingConfig,
    ) -> None:
        self._steps = steps
        self._exporter = exporter
        self._events = events
        self._clock = clock
        self._settle = timedelta(seconds=config.settle_seconds)
        self._max_lag = timedelta(seconds=config.max_lag_seconds)
        self._sweep_every = timedelta(seconds=config.sweep_seconds)
        self._batch_limit = config.batch_limit
        self._first_pass = True
        self._failing = False
        self._failures = 0
        self._next_due: datetime | None = None

    def sweep(self) -> None:
        now = self._clock.now()
        if self._next_due is not None and now < self._next_due:
            return
        newest = self._steps.newest_cursor()
        if self._first_pass:
            self._first_pass = False
            # A restart mid-outage must not announce the same failure again.
            self._failing = self._steps.newest_export_latch() == _FAILED
            jump = first_pass_jump(newest.position if newest else None, now, self._max_lag)
            if jump is not None:
                self._jump(jump, now)
                return
        assert newest is not None  # the first pass always leaves a cursor row behind
        cursor = newest.position
        window = read_window(self._steps, cursor, now - self._settle, self._batch_limit)
        oldest = window.steps[0].key.at if window.steps else None
        lag_jump = lag_cap_jump(cursor, oldest, now, self._max_lag)
        if lag_jump is not None:
            self._jump(lag_jump, now)
            return
        if not window.steps:
            if window.position != cursor:
                self._steps.append_cursor(TraceCursorRecord(window.position, 0, now))
            return
        spans = tuple(span for closed in window.steps for span in assemble_step(closed.facts, closed.step))
        if not self._export(spans):
            self._failed(now, len(window.steps))
            return
        self._steps.append_cursor(TraceCursorRecord(window.position, len(spans), self._clock.now()))
        self._recovered()
        _log.info("trace export sweep completed", steps=len(window.steps), spans=len(spans))

    def _export(self, spans: tuple[SpanRecord, ...]) -> bool:
        try:
            return self._exporter.export(spans)
        except Exception:
            _log.exception("trace exporter raised", spans=len(spans))
            return False

    def _failed(self, now: datetime, steps: int) -> None:
        self._failures += 1
        delay = backoff_delay(self._failures, self._sweep_every)
        self._next_due = now + delay
        _log.warning("trace export failed", steps=steps, failures=self._failures, retry_in=delay.total_seconds())
        if self._failing:
            return
        self._failing = True
        self._record(_FAILED, "fleet trace export failed; the cursor holds and the sweep retries with backoff", None)

    def _recovered(self) -> None:
        self._failures = 0
        self._next_due = None
        if not self._failing:
            return
        self._failing = False
        self._record(_RECOVERED, "fleet trace export recovered; held steps are being told", None)

    def _jump(self, jump: CursorJump, now: datetime) -> None:
        if jump.skipped_from is not None and self._held_a_step(jump.skipped_from, jump.to.at):
            self._record(
                _SKIPPED,
                f"fleet trace cursor jumped ({jump.reason.value}); the skipped window is told only by replay",
                {"reason": jump.reason.value, "since": _key_detail(jump.skipped_from), "until": iso_utc(jump.to.at)},
            )
        self._steps.append_cursor(TraceCursorRecord(jump.to, 0, now))

    def _held_a_step(self, since: CursorKey, until: datetime) -> bool:
        """Whether a step closed in ``(since, until)`` — read a step at a time, moving past closing
        facts that close nothing."""
        while True:
            window = read_window(self._steps, since, until - timedelta(microseconds=1), 1)
            if window.steps:
                return True
            if window.position == since:
                return False
            since = window.position

    def _record(self, kind: EventLogKind, message: str, detail: dict | None) -> None:  # type: ignore[type-arg]
        self._events.record(
            kind=kind,
            runner_id=None,
            chunk_id=None,
            lease_id=None,
            node_name=None,
            message=message,
            detail=detail,
            at=self._clock.now(),
        )
