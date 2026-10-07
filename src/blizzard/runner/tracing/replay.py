"""Replay: tells every lease that closed in ``[since, until)`` again, through the live sweep's assembly and ids.

Contract: ``blizzard-product:/delivered/tracing/runner-spans/spec/emission.md`` §Operator surface. It reads through
:class:`IReadLeaseTraces` alone, so it cannot move the cursor, and it records no latch: a replay leaves the live
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
from blizzard.runner.hub.identity import ICurrentRunnerIdentity
from blizzard.runner.tracing.assembly import assemble_lease
from blizzard.runner.tracing.cursor import LeaseCursorKey
from blizzard.runner.tracing.repository import IReadLeaseTraces

_log = get_logger("blizzard.runner.trace_export")


class ReplayWindowRefused(ValueError):
    """The window is inverted, empty, wider than ``replay_max_window``, or ends in the future."""


@domain_model
@dataclass(frozen=True)
class ReplayWindow:
    """A half-open replay window ``[since, until)`` no wider than the configured maximum."""

    since: datetime
    until: datetime

    @classmethod
    def of(cls, since: datetime, until: datetime, *, max_window_seconds: float, now: datetime) -> ReplayWindow:
        """The window, or :class:`ReplayWindowRefused` when it is inverted, empty, too wide, or ends after ``now``."""
        fault = OperatorWindow(since, until).fault(max_window=timedelta(seconds=max_window_seconds), now=now)
        if fault is not None:
            raise ReplayWindowRefused(
                fault_message(fault, max_window_name="replay_max_window", max_window_seconds=int(max_window_seconds))
            )
        return cls(since=since, until=until)

    @property
    def opening(self) -> LeaseCursorKey:
        """The read position before the window's first lease."""
        return LeaseCursorKey.opening(self.since)

    @property
    def last_inclusive(self) -> datetime:
        """The read's own inclusive upper bound — the instant just before ``until``."""
        return self.until - timedelta(microseconds=1)


class ReplayUnavailable(Exception):
    """A replay that cannot tell: a wet one with no exporter wired, as tracing is off, or any before the
    runner's first registration, when no span has a runner id to carry."""


@domain_model
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
    def __init__(
        self,
        *,
        leases: IReadLeaseTraces,
        exporter: ITraceExporter | None,
        identity: ICurrentRunnerIdentity,
        clock: IClock,
        config: TracingConfig,
    ) -> None:
        self._clock = clock
        self._leases = leases
        self._exporter = exporter
        self._identity = identity
        self._batch_limit = config.batch_limit
        self._max_window_seconds = config.replay_max_window

    def replay(self, since: datetime, until: datetime, *, dry_run: bool) -> ReplayResult:
        window = ReplayWindow.of(since, until, max_window_seconds=self._max_window_seconds, now=self._clock.now())
        if not dry_run and self._exporter is None:
            raise ReplayUnavailable("runner tracing is off; a replay without --dry-run has nowhere to send spans")
        if self._identity.current() is None:
            raise ReplayUnavailable("this runner has not registered with its hub yet, so its spans have no runner id")
        leases = spans = batches = 0
        position = window.opening
        last = window.last_inclusive
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

    def _export(self, spans: tuple[FinishedSpan, ...]) -> bool:
        assert self._exporter is not None
        try:
            return self._exporter.export(spans)
        except Exception:
            _log.exception("trace exporter raised during replay", spans=len(spans))
            return False
