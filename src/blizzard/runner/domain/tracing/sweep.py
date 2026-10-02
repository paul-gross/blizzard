"""The lease trace sweep: tells closed leases, in cursor order, to the configured exporter.

Contract: ``blizzard-product:/plans/tracing/runner-spans/spec/emission.md`` §Where it runs, §When a lease is
told and §The cursor, deferring to fleet-spans for the cursor's rules. Every collaborator is injected, so
:meth:`LeaseTraceSweep.sweep` is one complete, directly-callable pass (``bzh:steppable-loop``)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.event_log import EventLogKind
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.cursor import CursorJump, backoff_delay, first_pass_jump, lag_cap_jump
from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.foundation.trace_spans import SpanRecord
from blizzard.runner.domain.outbound import IWriteOutboundRepository, event_payload
from blizzard.runner.domain.tracing.assembly import assemble_lease
from blizzard.runner.domain.tracing.cursor import LeaseCursorKey
from blizzard.runner.domain.tracing.repository import IWriteLeaseTraces, LeaseCursorRecord
from blizzard.wire.facts import EVENT_RECORDED

_log = get_logger("blizzard.runner.trace_export")

# Recovered by the next pass re-reading the same leases from the unmoved cursor and re-sending them.
_CP_LEASETRACE_AFTER_EXPORT_BEFORE_CURSOR = crashpoint(
    "leasetrace.after-export.before-cursor",
    "the exporter accepted the batch; the cursor row that records it is not yet appended",
)

_FAILED: EventLogKind = "trace-export-failed"
_RECOVERED: EventLogKind = "trace-export-recovered"
_SKIPPED: EventLogKind = "trace-window-skipped"
_REJECTED: EventLogKind = "trace-config-rejected"


def _report(kind: EventLogKind, message: str, detail: dict[str, object] | None) -> str:
    return json.dumps(
        event_payload(kind=kind, chunk_id=None, lease_id=None, node_name=None, message=message, detail=detail)
    )


def announce_rejected_tracing(settings: TracingSettings, outbound: IWriteOutboundRepository, at: datetime) -> None:
    """A rejected tracing setting never stops the runner: it serves with tracing off and
    buffers one runner-wide ``trace-config-rejected`` per start, naming the setting."""
    if settings.state != "rejected":
        return
    payload = _report(
        _REJECTED, settings.rejection_message("runner"), {"setting": settings.setting, "value": settings.value}
    )
    outbound.enqueue_outbound(kind=EVENT_RECORDED, chunk_id=None, lease_id=None, payload=payload, created_at=at)


class LeaseTraceSweep:
    """Select, assemble, export, advance — the cursor moves only once the exporter accepts."""

    def __init__(
        self,
        *,
        leases: IWriteLeaseTraces,
        outbound: IWriteOutboundRepository,
        exporter: ITraceExporter,
        clock: IClock,
        config: TracingConfig,
    ) -> None:
        self._leases = leases
        self._outbound = outbound
        self._exporter = exporter
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
        newest = self._leases.newest_trace_cursor()
        if self._first_pass:
            self._first_pass = False
            # A restart mid-outage must not announce the same failure again.
            self._failing = self._leases.newest_trace_latch() == _FAILED
            jump = first_pass_jump(newest.position if newest else None, now, self._max_lag, key=LeaseCursorKey)
            if jump is not None:
                self._jump(jump, now)
                return
        assert newest is not None  # the first pass always leaves a cursor row behind
        cursor = newest.position
        keys = self._leases.closed_leases_after(cursor, now - self._settle, self._batch_limit)
        lag_jump = lag_cap_jump(cursor, keys[0].at if keys else None, now, self._max_lag)
        if lag_jump is not None:
            self._jump(lag_jump, now)
            return
        if not keys:
            return
        facts = self._leases.lease_trace_facts_for([k.lease_id for k in keys])
        spans = tuple(span for k in keys if k.lease_id in facts for span in assemble_lease(facts[k.lease_id]))
        if not spans:
            # A window of leases never minted to completion tells nothing, so there is nothing to export.
            self._leases.append_trace_cursor(LeaseCursorRecord(keys[-1], 0, now))
            return
        if not self._export(spans):
            self._failed(now, len(keys))
            return
        _CP_LEASETRACE_AFTER_EXPORT_BEFORE_CURSOR.reached()
        self._leases.append_trace_cursor(LeaseCursorRecord(keys[-1], len(spans), self._clock.now()))
        self._recovered()
        _log.info("lease trace sweep completed", leases=len(keys), spans=len(spans))

    def _export(self, spans: tuple[SpanRecord, ...]) -> bool:
        try:
            return self._exporter.export(spans)
        except Exception:
            _log.exception("trace exporter raised", spans=len(spans))
            return False

    def _failed(self, now: datetime, leases: int) -> None:
        self._failures += 1
        delay = backoff_delay(self._failures, self._sweep_every)
        self._next_due = now + delay
        _log.warning("trace export failed", leases=leases, failures=self._failures, retry_in=delay.total_seconds())
        if self._failing:
            return
        self._failing = True
        self._latch(_FAILED, "runner trace export failed; the cursor holds and the sweep retries with backoff")

    def _recovered(self) -> None:
        self._failures = 0
        self._next_due = None
        if not self._failing:
            return
        self._failing = False
        self._latch(_RECOVERED, "runner trace export recovered; held leases are being told")

    def _latch(self, kind: EventLogKind, message: str) -> None:
        self._leases.record_trace_latch(
            kind, at=self._clock.now(), report_kind=EVENT_RECORDED, report_payload=_report(kind, message, None)
        )

    def _jump(self, jump: CursorJump[LeaseCursorKey], now: datetime) -> None:
        since = jump.skipped_from
        if since is not None and self._leases.oldest_unsent_lease(since, jump.to.at - timedelta(microseconds=1)):
            detail: dict[str, object] = {
                "reason": jump.reason.value,
                "since": {"at": iso_utc(since.at), "lease_id": since.lease_id},
                "until": iso_utc(jump.to.at),
            }
            message = f"runner trace cursor jumped ({jump.reason.value}); the skipped window is told only by replay"
            self._outbound.enqueue_outbound(
                kind=EVENT_RECORDED,
                chunk_id=None,
                lease_id=None,
                payload=_report(_SKIPPED, message, detail),
                created_at=now,
            )
        self._leases.append_trace_cursor(LeaseCursorRecord(jump.to, 0, now))
