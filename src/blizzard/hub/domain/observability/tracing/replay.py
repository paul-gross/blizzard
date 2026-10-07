"""Replay: tells every step that closed and chunk that finished in ``[since, until)`` again, with the live ids.

Contract: ``blizzard-product:/delivered/tracing/fleet-spans/spec/emission.md`` §Operator surface. It reads through
:class:`IReadTraceSteps` alone, so it cannot move the cursor, and it records no event: a replay leaves the live
sweep's cursor, latch and backoff as they were."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.operator_window import OperatorWindow, fault_message
from blizzard.foundation.roles import domain_model
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.foundation.trace_spans import FinishedSpan
from blizzard.hub.domain.observability.tracing.cursor import CursorKey
from blizzard.hub.domain.observability.tracing.lifecycle import TraceVerb, trace_export_allows
from blizzard.hub.domain.observability.tracing.repository import IReadTraceSteps
from blizzard.hub.domain.observability.tracing.window import assemble_window, read_window

_log = get_logger("blizzard.hub.trace_export")


class ReplayWindowRefused(ValueError):
    """The window is inverted, empty, wider than ``replay_max_window``, or reaches past now."""


class ReplayUnavailable(Exception):
    """A wet replay with no exporter wired — tracing is off."""


def require_replayable(
    since: datetime, until: datetime, *, max_window_seconds: int, now: datetime, dry_run: bool, exporter_wired: bool
) -> None:
    """Refuse a replay the window or the wiring cannot honour — the window first, then the
    exporter. A dry run only counts, so it needs no exporter and runs with tracing off."""
    fault = OperatorWindow(since, until).fault(max_window=timedelta(seconds=max_window_seconds), now=now)
    if fault is not None:
        raise ReplayWindowRefused(
            fault_message(fault, max_window_name="replay_max_window", max_window_seconds=max_window_seconds)
        )
    if not trace_export_allows(TraceVerb.DRY_REPLAY if dry_run else TraceVerb.REPLAY, exporter_wired=exporter_wired):
        raise ReplayUnavailable("fleet tracing is off; a replay without --dry-run has nowhere to send spans")


@domain_model
@dataclass(frozen=True)
class ReplayResult:
    """What a replay told, or with ``dry_run`` would have told. ``failed`` is set when the exporter refused
    or raised: the counts are then what it accepted before, and the replay stopped."""

    steps: int
    spans: int
    batches: int
    dry_run: bool
    failed: bool = False
    chunks: int = 0


class TraceReplay:
    def __init__(
        self, *, steps: IReadTraceSteps, exporter: ITraceExporter | None, clock: IClock, config: TracingConfig
    ) -> None:
        self._steps = steps
        self._exporter = exporter
        self._clock = clock
        self._batch_limit = config.batch_limit
        self._max_window_seconds = config.replay_max_window

    def replay(self, since: datetime, until: datetime, *, dry_run: bool) -> ReplayResult:
        require_replayable(
            since,
            until,
            max_window_seconds=self._max_window_seconds,
            now=self._clock.now(),
            dry_run=dry_run,
            exporter_wired=self._exporter is not None,
        )
        steps = chunks = spans = batches = 0
        position = CursorKey.opening(since)
        while True:
            # The window is half-open: read_window's own bound is inclusive.
            window = read_window(self._steps, position, until - timedelta(microseconds=1), self._batch_limit)
            if window.items:
                told = assemble_window(window)
                if not dry_run and not self._export(told):
                    return ReplayResult(steps, spans, batches, dry_run, failed=True, chunks=chunks)
                steps += len(window.closed_steps())
                chunks += len(window.finished_chunks())
                spans += len(told)
                batches += 1
            if window.position == position:
                return ReplayResult(steps, spans, batches, dry_run, chunks=chunks)
            position = window.position

    def _export(self, spans: tuple[FinishedSpan, ...]) -> bool:
        assert self._exporter is not None
        try:
            return self._exporter.export(spans)
        except Exception:
            _log.exception("trace exporter raised during replay", spans=len(spans))
            return False
