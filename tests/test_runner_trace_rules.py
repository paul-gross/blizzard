"""Pure rules of runner tracing — the replay window, the sweep's cursor and announcements, the
status classifications, the token bucket, and span routing. No store, no clock: every instant
is passed in."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.cli_spans import SCOPE_NAME
from blizzard.foundation.platform_tracing.handle import DisabledPlatformTracing
from blizzard.foundation.platform_tracing.received import ReceivedDataPoint, ReceivedMetrics, ReceivedSpan
from blizzard.foundation.trace_export.cursor import CursorJump, JumpReason
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.foundation.trace_ids import chunk_trace_id
from blizzard.runner.harness.claude_code.telemetry_plan import (
    CLAUDE_CODE_METRICS_SCOPE,
    CLAUDE_CODE_SERVICE_NAME,
    CLAUDE_CODE_TELEMETRY_NAMES,
    CLAUDE_CODE_TRACING_SCOPE,
)
from blizzard.runner.harness.harness_telemetry_plan import HarnessTelemetryNames
from blizzard.runner.hub.identity import RunnerIdentity, RunnerIdentityHolder
from blizzard.runner.leases.model import Lease
from blizzard.runner.tracing.cursor import LeaseCursorKey
from blizzard.runner.tracing.received_export import DisabledReceivedTelemetryExport
from blizzard.runner.tracing.receiver import (
    PROGRAM_SERVICE_NAME,
    route_spans,
    service_name_for,
)
from blizzard.runner.tracing.receiver_limits import (
    Bucket,
    ReceiverBounds,
    ReceiverCount,
    ReceiverCounter,
    SpanRateLimiter,
)
from blizzard.runner.tracing.receiving import RateExceeded, ReceiverOff, TelemetryReceiver
from blizzard.runner.tracing.replay import ReplayWindow, ReplayWindowRefused
from blizzard.runner.tracing.repository import LeaseTraceCheckpoint, LeaseTraceExportFailure
from blizzard.runner.tracing.status import cursor_lag, export_failure_ongoing
from blizzard.runner.tracing.sweep import cursor_after, rejected_tracing_report, skipped_window_report
from tests.runner_fakes import REGISTERED_AT

pytestmark = pytest.mark.unit

_RUNNER = RunnerIdentity(runner_id="r1", runner_name="r-claude", registered_at=REGISTERED_AT)

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _lease() -> Lease:
    return Lease(
        lease_id="lease_1",
        chunk_id="ch_1",
        graph_id="gr_1",
        node_id="nd_build",
        node_name="build",
        epoch=1,
        retries_max=2,
        created_at=_T0,
    )


def _span(scope: str, **changes: object) -> ReceivedSpan:
    fields: dict[str, object] = {
        "trace_id": chunk_trace_id("ch_1"),
        "span_id": 0x00F067AA0BA902B7,
        "parent_span_id": None,
        "name": "work",
        "kind": 1,
        "start_time_ns": 1_000,
        "end_time_ns": 2_000,
        "status_code": 1,
        "scope_name": scope,
        "scope_version": "1",
        "attributes": {},
    }
    fields.update(changes)
    return ReceivedSpan(**fields)  # type: ignore[arg-type]


# --- replay ---------------------------------------------------------------------------


def test_replay_window_refuses_empty_oversized_and_future() -> None:
    now = _T0 + timedelta(hours=1)
    with pytest.raises(ReplayWindowRefused, match="until must be after since"):
        ReplayWindow.of(_T0, _T0, max_window_seconds=60, now=now)
    with pytest.raises(ReplayWindowRefused, match=r"wider than replay_max_window \(60 seconds\)"):
        ReplayWindow.of(_T0, _T0 + timedelta(seconds=61), max_window_seconds=60, now=now)
    with pytest.raises(ReplayWindowRefused, match="until must not be in the future"):
        ReplayWindow.of(now - timedelta(seconds=30), now + timedelta(seconds=1), max_window_seconds=60, now=now)
    window = ReplayWindow.of(_T0, _T0 + timedelta(seconds=60), max_window_seconds=60, now=now)
    assert window.opening == LeaseCursorKey(_T0)
    assert window.last_inclusive == _T0 + timedelta(seconds=60) - timedelta(microseconds=1)


# --- the sweep ------------------------------------------------------------------------


def test_empty_window_advances_cursor() -> None:
    keys = [LeaseCursorKey(_T0, "lease_1"), LeaseCursorKey(_T0, "lease_2")]
    assert cursor_after(keys, 0, _T0) == LeaseTraceCheckpoint(LeaseCursorKey(_T0, "lease_2"), 0, _T0)


def test_skipped_window_report_only_with_unsent() -> None:
    since = LeaseCursorKey(_T0, "lease_1")
    jump = CursorJump(JumpReason.LAG_CAP, LeaseCursorKey(_T0 + timedelta(hours=1)), skipped_from=since)
    assert skipped_window_report(jump, unsent=False) is None
    assert skipped_window_report(CursorJump(JumpReason.START, since), unsent=True) is None
    report = skipped_window_report(jump, unsent=True)
    assert report is not None
    payload = json.loads(report)
    assert payload["kind"] == "trace-window-skipped"
    assert payload["detail"] == {
        "reason": "lag-cap",
        "since": {"at": "2026-01-01T00:00:00+00:00", "lease_id": "lease_1"},
        "until": "2026-01-01T01:00:00+00:00",
    }


def test_rejected_tracing_report() -> None:
    assert rejected_tracing_report(TracingSettings(state="disabled")) is None
    report = rejected_tracing_report(TracingSettings(state="rejected", setting="OTEL_PROTOCOL", value="grpc"))
    assert report is not None
    payload = json.loads(report)
    assert payload["kind"] == "trace-config-rejected"
    assert payload["detail"] == {"setting": "OTEL_PROTOCOL", "value": "grpc"}
    assert "OTEL_PROTOCOL='grpc' is not supported" in payload["message"]


# --- status ---------------------------------------------------------------------------


def test_cursor_lag_never_negative() -> None:
    assert cursor_lag(None, _T0) is None
    assert cursor_lag(_T0, _T0 + timedelta(seconds=5)) == 5.0
    assert cursor_lag(_T0 + timedelta(seconds=5), _T0) == 0.0


def test_export_failure_ongoing() -> None:
    failure = LeaseTraceExportFailure(_T0)
    assert export_failure_ongoing(failure, "trace-export-failed") is True
    assert export_failure_ongoing(failure, "trace-export-recovered") is False
    assert export_failure_ongoing(None, "trace-export-failed") is False


# --- the token bucket -----------------------------------------------------------------


def test_bucket_refill_and_take_with_explicit_now() -> None:
    bucket = Bucket.full(10, _T0)
    taken = bucket.take(10)
    assert taken == Bucket(0.0, _T0)
    assert taken is not None
    assert taken.take(1) is None
    refilled = taken.refilled(_T0 + timedelta(seconds=2), capacity=10, refill_per_second=2.0)
    assert refilled == Bucket(4.0, _T0 + timedelta(seconds=2))
    assert refilled.refilled(_T0 + timedelta(hours=1), capacity=10, refill_per_second=2.0).level == 10.0
    assert bucket.idle(_T0 + timedelta(seconds=5), capacity=10, refill_per_second=2.0) is True
    assert bucket.idle(_T0 + timedelta(seconds=4), capacity=10, refill_per_second=2.0) is False


_NAMES = (CLAUDE_CODE_TELEMETRY_NAMES,)
_OTHER_NAMES = HarnessTelemetryNames(
    traces_scope="other.tracing", metrics_scope="other", logs_scope="other.events", service_name="blizzard-other"
)


# --- span routing and the receiver ----------------------------------------------------


def test_route_spans_keeps_the_cli_and_refuses_other_programs_by_default() -> None:
    routing = route_spans(
        [_span(SCOPE_NAME), _span("some.library")],
        _lease(),
        runner=_RUNNER,
        programs=False,
        harness=False,
        names=_NAMES,
    )
    assert ([s.scope_name for s in routing.cli], routing.others) == ([SCOPE_NAME], [])
    assert (routing.accepted, routing.dropped, routing.kept, routing.refused, routing.rest_received) == (1, 1, 1, 1, 2)


def test_route_spans_admits_other_programs_under_worker_programs() -> None:
    routing = route_spans(
        [_span(SCOPE_NAME), _span("some.library")], _lease(), runner=_RUNNER, programs=True, harness=False, names=_NAMES
    )
    assert [s.scope_name for s in routing.cli] == [SCOPE_NAME]
    assert [s.scope_name for s in routing.others] == ["some.library"]
    assert (routing.accepted, routing.dropped) == (2, 0)


def test_route_spans_keeps_claude_codes_scope_only_under_harness_telemetry() -> None:
    claude = _span(CLAUDE_CODE_TRACING_SCOPE)
    on = route_spans([claude], _lease(), runner=_RUNNER, programs=False, harness=True, names=_NAMES)
    assert (len(on.harness.kept), on.harness_received, on.rest_received) == (1, 1, 0)
    off = route_spans([claude], _lease(), runner=_RUNNER, programs=False, harness=False, names=_NAMES)
    assert (off.harness_received, off.rest_received, off.dropped) == (0, 1, 1)


def test_route_spans_iterates_the_bindings_it_is_given_not_claude_code() -> None:
    other, claude = _span("other.tracing"), _span(CLAUDE_CODE_TRACING_SCOPE)
    routing = route_spans(
        [other, claude], _lease(), runner=_RUNNER, programs=False, harness=True, names=(_OTHER_NAMES,)
    )
    assert (len(routing.harness.kept), routing.harness_received, routing.rest_received) == (1, 1, 1)
    assert service_name_for("other.events", {}, (_OTHER_NAMES,)) == "blizzard-other"
    assert service_name_for(CLAUDE_CODE_TRACING_SCOPE, {}, (_OTHER_NAMES,)) == PROGRAM_SERVICE_NAME


def test_service_name_for() -> None:
    assert service_name_for("some.library", {}, _NAMES) == PROGRAM_SERVICE_NAME
    assert service_name_for(CLAUDE_CODE_TRACING_SCOPE, {}, _NAMES) == CLAUDE_CODE_SERVICE_NAME
    assert service_name_for("some.library", {"some.library": "mine"}, _NAMES) == "mine"


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def _receiver(*, enabled: bool, harness: bool = True, capacity: int = 1000) -> TelemetryReceiver:
    clock = _FixedClock(_T0)

    class _Tracing(DisabledPlatformTracing):
        @property
        def enabled(self) -> bool:  # type: ignore[override]
            return enabled

    return TelemetryReceiver(
        platform_tracing=_Tracing(),
        received_telemetry=DisabledReceivedTelemetryExport(),
        span_limiter=SpanRateLimiter(clock, capacity=capacity),
        span_counter=ReceiverCounter(),
        harness_span_counter=ReceiverCounter(),
        metric_bounds=ReceiverBounds(SpanRateLimiter(clock, capacity=capacity), ReceiverCounter()),
        log_bounds=ReceiverBounds.fresh(clock),
        clock=clock,
        identity=RunnerIdentityHolder(_RUNNER),
        harness_telemetry=harness,
        telemetry_names=_NAMES,
    )


def test_receiver_off_when_platform_disabled() -> None:
    with pytest.raises(ReceiverOff, match="platform tracing is off"):
        _receiver(enabled=False).require_traces()
    with pytest.raises(ReceiverOff, match="harness telemetry is off"):
        _receiver(enabled=True, harness=False).require_harness_telemetry()
    _receiver(enabled=True).require_traces()


def test_span_rate_refused_counts_every_span_dropped() -> None:
    receiver = _receiver(enabled=True, capacity=1)
    with pytest.raises(RateExceeded, match="span rate exceeded"):
        receiver.receive_spans(_lease(), [_span(SCOPE_NAME), _span(CLAUDE_CODE_TRACING_SCOPE)])
    assert (receiver.span_counter.count().dropped, receiver.harness_span_counter.count().dropped) == (1, 1)


def _point() -> ReceivedDataPoint:
    return ReceivedDataPoint(
        metric_name="tokens",
        description="",
        unit="1",
        kind="sum",
        temporality=1,
        monotonic=True,
        scope_name=CLAUDE_CODE_METRICS_SCOPE,
        scope_version="1",
        start_time_ns=1_000,
        time_ns=2_000,
        value=3,
    )


def test_metrics_rate_refused() -> None:
    receiver = _receiver(enabled=True, capacity=1)
    with pytest.raises(RateExceeded, match="rate exceeded"):
        receiver.receive_metrics(_lease(), ReceivedMetrics(points=[_point(), _point()], unsupported=1))
    assert receiver.metric_bounds.counter.count() == ReceiverCount(accepted=0, dropped=3)


def test_metrics_within_rate_name_the_summary_points_refused() -> None:
    receiver = _receiver(enabled=True)
    assert receiver.receive_metrics(_lease(), ReceivedMetrics(points=[_point()], unsupported=2)) == 2
    assert (receiver.metric_bounds.counter.count().accepted, receiver.metric_bounds.counter.count().dropped) == (1, 2)
