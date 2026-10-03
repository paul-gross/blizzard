"""Replay: tells every step that closed in ``[since, until)`` again, through the live sweep's assembly and ids.

Contract: ``blizzard-product:/delivered/tracing/fleet-spans/spec/emission.md`` §Operator surface. It reads through
:class:`IReadTraceSteps` alone, so it cannot move the cursor, and it records no event: a replay leaves the live
sweep's cursor, latch and backoff as they were."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from blizzard.foundation.logging import get_logger
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.foundation.trace_spans import SpanRecord
from blizzard.hub.domain.tracing.cursor import CursorKey
from blizzard.hub.domain.tracing.repository import IReadTraceSteps
from blizzard.hub.domain.tracing.window import assemble_window, read_window

_log = get_logger("blizzard.hub.trace_export")


class ReplayWindowRefused(ValueError):
    """The window is inverted, empty, or wider than ``replay_max_window``."""


class ReplayUnavailable(Exception):
    """A wet replay with no exporter wired — tracing is off."""


@dataclass(frozen=True)
class ReplayResult:
    """What a replay told, or with ``dry_run`` would have told. ``failed`` is set when the exporter refused
    or raised: the counts are then what it accepted before, and the replay stopped."""

    steps: int
    spans: int
    batches: int
    dry_run: bool
    failed: bool = False


class TraceReplay:
    def __init__(self, *, steps: IReadTraceSteps, exporter: ITraceExporter | None, config: TracingConfig) -> None:
        self._steps = steps
        self._exporter = exporter
        self._batch_limit = config.batch_limit
        self._max_window = timedelta(seconds=config.replay_max_window)
        self._max_window_seconds = config.replay_max_window

    def replay(self, since: datetime, until: datetime, *, dry_run: bool) -> ReplayResult:
        if until <= since:
            raise ReplayWindowRefused("until must be after since")
        if until - since > self._max_window:
            raise ReplayWindowRefused(f"window is wider than replay_max_window ({self._max_window_seconds} seconds)")
        if not dry_run and self._exporter is None:
            raise ReplayUnavailable("fleet tracing is off; a replay without --dry-run has nowhere to send spans")
        steps = spans = batches = 0
        position = CursorKey.opening(since)
        while True:
            # The window is half-open: read_window's own bound is inclusive.
            window = read_window(self._steps, position, until - timedelta(microseconds=1), self._batch_limit)
            if window.steps:
                told = assemble_window(window)
                if not dry_run and not self._export(told):
                    return ReplayResult(steps, spans, batches, dry_run, failed=True)
                steps += len(window.steps)
                spans += len(told)
                batches += 1
            if window.position == position:
                return ReplayResult(steps, spans, batches, dry_run)
            position = window.position

    def _export(self, spans: tuple[SpanRecord, ...]) -> bool:
        assert self._exporter is not None
        try:
            return self._exporter.export(spans)
        except Exception:
            _log.exception("trace exporter raised during replay", spans=len(spans))
            return False
