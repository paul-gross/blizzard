"""Replay: tells every lease that closed in ``[since, until)`` again, through the live sweep's assembly and ids.

Contract: ``blizzard-product:/delivered/tracing/runner-spans/spec/emission.md`` §Operator surface. It reads through
:class:`IReadLeaseTraces` alone, so it cannot move the cursor, and it records no latch: a replay leaves the live
sweep's cursor, latch and backoff as they were."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from blizzard.foundation.logging import get_logger
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.foundation.trace_spans import SpanRecord
from blizzard.runner.domain.tracing.assembly import assemble_lease
from blizzard.runner.domain.tracing.cursor import LeaseCursorKey
from blizzard.runner.domain.tracing.repository import IReadLeaseTraces

_log = get_logger("blizzard.runner.trace_export")


class ReplayWindowRefused(ValueError):
    """The window is inverted, empty, or wider than ``replay_max_window``."""


class ReplayUnavailable(Exception):
    """A wet replay with no exporter wired — tracing is off."""


@dataclass(frozen=True)
class ReplayResult:
    """What a replay told, or with ``dry_run`` would have told. ``failed`` is set when the exporter refused
    or raised: the counts are then what it accepted before, and the replay stopped."""

    leases: int
    spans: int
    batches: int
    dry_run: bool
    failed: bool = False


class LeaseTraceReplay:
    def __init__(self, *, leases: IReadLeaseTraces, exporter: ITraceExporter | None, config: TracingConfig) -> None:
        self._leases = leases
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
            raise ReplayUnavailable("runner tracing is off; a replay without --dry-run has nowhere to send spans")
        leases = spans = batches = 0
        position = LeaseCursorKey.opening(since)
        # The window is half-open: the read's own bound is inclusive.
        last = until - timedelta(microseconds=1)
        while True:
            keys = self._leases.closed_leases_after(position, last, self._batch_limit)
            if not keys:
                return ReplayResult(leases, spans, batches, dry_run)
            facts = self._leases.lease_trace_facts_for([k.lease_id for k in keys])
            told = tuple(span for k in keys if k.lease_id in facts for span in assemble_lease(facts[k.lease_id]))
            if told:
                if not dry_run and not self._export(told):
                    return ReplayResult(leases, spans, batches, dry_run, failed=True)
                spans += len(told)
                batches += 1
            leases += sum(1 for k in keys if k.lease_id in facts)
            position = keys[-1]

    def _export(self, spans: tuple[SpanRecord, ...]) -> bool:
        assert self._exporter is not None
        try:
            return self._exporter.export(spans)
        except Exception:
            _log.exception("trace exporter raised during replay", spans=len(spans))
            return False
