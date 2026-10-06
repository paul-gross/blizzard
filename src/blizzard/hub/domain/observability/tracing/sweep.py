"""The trace export sweep: tells closed node steps and finished chunks, in cursor order, to the configured exporter.

Contract: ``blizzard-product:/delivered/tracing/fleet-spans/spec/emission.md`` §What a sweep does,
§The cursor and §Delivery semantics. Every collaborator is injected, so :meth:`TraceExportSweep.sweep`
is one complete, directly-callable pass (``bzh:steppable-loop``)."""

from __future__ import annotations

from datetime import datetime, timedelta

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.event_log import EventLogKind
from blizzard.foundation.lane_retry import OutageLatch
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.cursor import CursorJump, first_pass_jump, lag_cap_jump
from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.foundation.trace_spans import FinishedSpan
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.observability.tracing.cursor import CursorKey
from blizzard.hub.domain.observability.tracing.repository import IWriteTraceCursor, TraceCheckpoint
from blizzard.hub.domain.observability.tracing.window import assemble_window, oldest_unsent, read_window

_log = get_logger("blizzard.hub.trace_export")

# The exporter accepted the batch; the cursor row that records it is not yet appended.
# Recovered by the next pass re-reading the same steps from the unmoved cursor and re-sending them.
_CP_TRACE_AFTER_EXPORT_BEFORE_CURSOR = crashpoint(
    "trace.after-export.before-cursor",
    "the exporter accepted the batch; the cursor row that records it is not yet appended",
)

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
        self._batch_limit = config.batch_limit
        # A restart mid-outage must not announce the same failure again.
        self._latch = OutageLatch(
            timedelta(seconds=config.sweep_seconds), lambda: steps.newest_export_latch() == _FAILED
        )
        self._cursor_started = False

    def sweep(self) -> None:
        now = self._clock.now()
        if not self._latch.is_due(now):
            return
        newest = self._steps.newest_cursor()
        if not self._cursor_started:
            jump = first_pass_jump(newest.position if newest else None, now, self._max_lag, key=CursorKey)
            if jump is not None:
                self._jump(jump, now)
                self._cursor_started = True
                return
            self._cursor_started = True
        assert newest is not None  # the first pass always leaves a cursor row behind
        cursor = newest.position
        window = read_window(self._steps, cursor, now - self._settle, self._batch_limit)
        oldest = window.items[0].key.at if window.items else None
        lag_jump = lag_cap_jump(cursor, oldest, now, self._max_lag)
        if lag_jump is not None:
            self._jump(lag_jump, now)
            return
        if not window.items:
            if window.position != cursor:
                self._steps.append_cursor(TraceCheckpoint(window.position, 0, now))
            return
        spans = assemble_window(window)
        if not self._export(spans):
            self._failed(now, len(window.items))
            return
        _CP_TRACE_AFTER_EXPORT_BEFORE_CURSOR.reached()
        self._steps.append_cursor(TraceCheckpoint(window.position, len(spans), self._clock.now()))
        self._recovered()
        _log.info(
            "trace export sweep completed",
            steps=len(window.closed_steps()),
            chunks=len(window.finished_chunks()),
            spans=len(spans),
        )

    def _export(self, spans: tuple[FinishedSpan, ...]) -> bool:
        try:
            return self._exporter.export(spans)
        except Exception:
            _log.exception("trace exporter raised", spans=len(spans))
            return False

    def _failed(self, now: datetime, items: int) -> None:
        opens = self._latch.failed(now)
        _log.warning(
            "trace export failed",
            items=items,
            failures=self._latch.failures,
            retry_in=self._latch.retry_in.total_seconds(),
        )
        if opens:
            self._record(
                _FAILED, "fleet trace export failed; the cursor holds and the sweep retries with backoff", None
            )

    def _recovered(self) -> None:
        if self._latch.succeeded():
            self._record(_RECOVERED, "fleet trace export recovered; held steps are being told", None)

    def _jump(self, jump: CursorJump[CursorKey], now: datetime) -> None:
        since = jump.skipped_from
        unsent = None if since is None else oldest_unsent(self._steps, since, jump.to.at - timedelta(microseconds=1))
        self._steps.append_cursor(TraceCheckpoint(jump.to, 0, now))
        if since is not None and unsent is not None:
            self._record(
                _SKIPPED,
                f"fleet trace cursor jumped ({jump.reason.value}); the skipped window is told only by replay",
                {"reason": jump.reason.value, "since": _key_detail(since), "until": iso_utc(jump.to.at)},
            )

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
