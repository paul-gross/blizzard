"""The lease trace sweep: tells closed leases, in cursor order, to the configured exporter.

Contract: ``blizzard-product:/delivered/tracing/runner-spans/spec/emission.md`` §Where it runs, §When a lease is
told and §The cursor, deferring to fleet-spans for the cursor's rules. Every collaborator is injected, so
:meth:`LeaseTraceSweep.sweep` is one complete, directly-callable pass (``bzh:steppable-loop``)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timedelta

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.event_log import EventLogKind
from blizzard.foundation.fact_kinds import EVENT_RECORDED
from blizzard.foundation.lane_retry import OutageLatch
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.cursor import CursorJump, first_pass_jump, lag_cap_jump
from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.foundation.trace_spans import FinishedSpan
from blizzard.runner.hub.identity import ICurrentRunnerIdentity
from blizzard.runner.hub.outbound_buffer import IWriteOutboundRepository, event_payload
from blizzard.runner.tracing.assembly import assemble_lease
from blizzard.runner.tracing.cursor import LeaseCursorKey
from blizzard.runner.tracing.repository import IWriteLeaseTraces, LeaseTraceCheckpoint

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


#: The one message a runner export failure carries — the exporter's own error is never kept.
FAILED_MESSAGE = "runner trace export failed; the cursor holds and the sweep retries with backoff"


def _report(kind: EventLogKind, message: str, detail: dict[str, object] | None) -> str:
    return json.dumps(
        event_payload(kind=kind, chunk_id=None, lease_id=None, node_name=None, message=message, detail=detail)
    )


def rejected_tracing_report(settings: TracingSettings) -> str | None:
    """The ``trace-config-rejected`` event payload a rejected tracing setting owes, naming the
    setting — ``None`` for an accepted one. A rejection never stops the runner: it serves with
    tracing off."""
    if settings.state != "rejected":
        return None
    return _report(
        _REJECTED, settings.rejection_message("runner"), {"setting": settings.setting, "value": settings.value}
    )


def announce_rejected_tracing(settings: TracingSettings, outbound: IWriteOutboundRepository, at: datetime) -> None:
    """Buffer one runner-wide ``trace-config-rejected`` per start when the setting was rejected."""
    payload = rejected_tracing_report(settings)
    if payload is None:
        return
    outbound.enqueue_outbound(kind=EVENT_RECORDED, chunk_id=None, lease_id=None, payload=payload, created_at=at)


def cursor_after(keys: Sequence[LeaseCursorKey], span_count: int, at: datetime) -> LeaseTraceCheckpoint:
    """The checkpoint a told window leaves: past its last lease, with the spans it told. A
    window of leases never minted to completion tells nothing, yet still advances the cursor."""
    return LeaseTraceCheckpoint(keys[-1], span_count, at)


def skipped_window_report(jump: CursorJump[LeaseCursorKey], *, unsent: bool) -> str | None:
    """The ``trace-window-skipped`` event payload a cursor jump owes — only when the window it
    skips holds a lease never told (``unsent``); replay is then the only way it is told."""
    since = jump.skipped_from
    if since is None or not unsent:
        return None
    detail: dict[str, object] = {
        "reason": jump.reason.value,
        "since": {"at": iso_utc(since.at), "lease_id": since.lease_id},
        "until": iso_utc(jump.to.at),
    }
    message = f"runner trace cursor jumped ({jump.reason.value}); the skipped window is told only by replay"
    return _report(_SKIPPED, message, detail)


class LeaseTraceSweep:
    """Select, assemble, export, advance — the cursor moves only once the exporter accepts. Before the
    runner's first registration it holds: a span has no runner id to carry yet, and the leases closing
    meanwhile wait for one rather than being passed over."""

    def __init__(
        self,
        *,
        leases: IWriteLeaseTraces,
        outbound: IWriteOutboundRepository,
        exporter: ITraceExporter,
        identity: ICurrentRunnerIdentity,
        clock: IClock,
        config: TracingConfig,
    ) -> None:
        self._leases = leases
        self._identity = identity
        self._outbound = outbound
        self._exporter = exporter
        self._clock = clock
        self._settle = timedelta(seconds=config.settle_seconds)
        self._max_lag = timedelta(seconds=config.max_lag_seconds)
        self._batch_limit = config.batch_limit
        # A restart mid-outage must not announce the same failure again.
        self._latch = OutageLatch(
            timedelta(seconds=config.sweep_seconds), lambda: leases.newest_trace_latch() == _FAILED
        )
        self._cursor_started = False

    def sweep(self) -> None:
        if self._identity.current() is None:
            return
        now = self._clock.now()
        if not self._latch.is_due(now):
            return
        newest = self._leases.newest_trace_cursor()
        if not self._cursor_started:
            self._cursor_started = True
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
            self._leases.append_trace_cursor(cursor_after(keys, 0, now))
            return
        if not self._export(spans):
            self._failed(now, len(keys))
            return
        _CP_LEASETRACE_AFTER_EXPORT_BEFORE_CURSOR.reached()
        self._leases.append_trace_cursor(cursor_after(keys, len(spans), self._clock.now()))
        self._recovered()
        _log.info("lease trace sweep completed", leases=len(keys), spans=len(spans))

    def _export(self, spans: tuple[FinishedSpan, ...]) -> bool:
        try:
            return self._exporter.export(spans)
        except Exception:
            _log.exception("trace exporter raised", spans=len(spans))
            return False

    def _failed(self, now: datetime, leases: int) -> None:
        opens = self._latch.failed(now)
        _log.warning(
            "trace export failed",
            leases=leases,
            failures=self._latch.failures,
            retry_in=self._latch.retry_in.total_seconds(),
        )
        if opens:
            self._announce(_FAILED, FAILED_MESSAGE)

    def _recovered(self) -> None:
        if self._latch.succeeded():
            self._announce(_RECOVERED, "runner trace export recovered; held leases are being told")

    def _announce(self, kind: EventLogKind, message: str) -> None:
        self._leases.record_trace_latch(
            kind, at=self._clock.now(), report_kind=EVENT_RECORDED, report_payload=_report(kind, message, None)
        )

    def _jump(self, jump: CursorJump[LeaseCursorKey], now: datetime) -> None:
        since = jump.skipped_from
        unsent = since is not None and bool(
            self._leases.oldest_unsent_lease(since, jump.to.at - timedelta(microseconds=1))
        )
        payload = skipped_window_report(jump, unsent=unsent)
        if payload is not None:
            self._outbound.enqueue_outbound(
                kind=EVENT_RECORDED, chunk_id=None, lease_id=None, payload=payload, created_at=now
            )
        self._leases.append_trace_cursor(LeaseTraceCheckpoint(jump.to, 0, now))
