"""Received metrics and logs (unit tier) — the per-signal export read, decoding both encodings, admission, the
response encoders, the export handle's switches, and the paths that make no span."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from google.protobuf.json_format import MessageToDict

from blizzard.foundation.platform_tracing.exclusion import is_excluded
from blizzard.foundation.platform_tracing.received import (
    JSON_CONTENT_TYPE,
    PROTOBUF_CONTENT_TYPE,
    ExponentialBuckets,
    OtlpDecodeError,
    ReceivedDataPoint,
    ReceivedLogRecord,
    decode_logs,
    decode_metrics,
    rejected_data_points,
    rejected_log_records,
)
from blizzard.foundation.platform_tracing.received_export import (
    DisabledReceivedTelemetryExport,
    build_received_telemetry_export,
)
from blizzard.foundation.platform_tracing.signals import TelemetrySignal, signal_exportable
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.foundation.trace_ids import chunk_trace_id
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.leases import Lease
from blizzard.runner.tracing.receiver import (
    MAX_ATTRIBUTES,
    MAX_STRING_CHARS,
    admit_data_points,
    admit_log_records,
)
from tests import claude_code_telemetry
from tests.runner_fakes import REGISTERED_AT

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)
_METRICS_SCOPE = "com.anthropic.claude_code"
_LOGS_SCOPE = "com.anthropic.claude_code.events"
_RUNNER = RunnerIdentity(runner_id="r1", runner_name="r-claude", registered_at=REGISTERED_AT)
_ENDPOINT = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}
_ENCODINGS = [
    (PROTOBUF_CONTENT_TYPE, claude_code_telemetry.protobuf_body),
    (JSON_CONTENT_TYPE, claude_code_telemetry.json_body),
]


def _lease() -> Lease:
    return Lease(
        lease_id="lease_1",
        chunk_id="ch_1",
        graph_id="gr_1",
        node_id="nd_build",
        node_name="build",
        epoch=1,
        retries_max=2,
        created_at=_NOW,
    )


def _point(**changes: object) -> ReceivedDataPoint:
    fields: dict[str, object] = {
        "metric_name": "m",
        "description": "d",
        "unit": "s",
        "kind": "sum",
        "temporality": 1,
        "monotonic": True,
        "scope_name": _METRICS_SCOPE,
        "scope_version": "1",
        "start_time_ns": 1,
        "time_ns": 2,
        "value": 3,
    }
    fields.update(changes)
    return ReceivedDataPoint(**fields)  # type: ignore[arg-type]


def _record(**changes: object) -> ReceivedLogRecord:
    fields: dict[str, object] = {
        "time_ns": 1,
        "observed_time_ns": 2,
        "severity_number": 9,
        "severity_text": "INFO",
        "body": "event",
        "trace_id": None,
        "span_id": None,
        "trace_flags": 0,
        "scope_name": _LOGS_SCOPE,
        "scope_version": "1",
    }
    fields.update(changes)
    return ReceivedLogRecord(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize("signal", list(TelemetrySignal))
def test_a_signal_needs_its_own_or_the_general_endpoint(signal: TelemetrySignal) -> None:
    assert not signal_exportable(signal, {})
    assert signal_exportable(signal, _ENDPOINT)
    assert signal_exportable(signal, {signal.endpoint_variable: "http://collector:4318"})
    other = next(other for other in TelemetrySignal if other is not signal)
    assert not signal_exportable(signal, {other.endpoint_variable: "http://collector:4318"})


@pytest.mark.parametrize("signal", list(TelemetrySignal))
def test_a_signal_is_off_when_its_exporter_or_the_sdk_is_switched_off(signal: TelemetrySignal) -> None:
    assert not signal_exportable(signal, {**_ENDPOINT, signal.exporter_variable: "none"})
    assert not signal_exportable(signal, {**_ENDPOINT, "OTEL_SDK_DISABLED": "true"})
    assert signal_exportable(signal, {**_ENDPOINT, signal.exporter_variable: "otlp"})


@pytest.mark.parametrize("signal", list(TelemetrySignal))
def test_only_http_protobuf_exports_and_the_signals_own_protocol_wins(signal: TelemetrySignal) -> None:
    assert not signal_exportable(signal, {**_ENDPOINT, "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"})
    assert not signal_exportable(signal, {**_ENDPOINT, signal.protocol_variable: "http/json"})
    assert signal_exportable(
        signal, {**_ENDPOINT, "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc", signal.protocol_variable: "http/protobuf"}
    )


@pytest.mark.parametrize(
    "environ",
    [
        {},
        _ENDPOINT,
        {**_ENDPOINT, "OTEL_TRACES_EXPORTER": "none"},
        {**_ENDPOINT, "OTEL_SDK_DISABLED": "TRUE"},
        {**_ENDPOINT, "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"},
        {**_ENDPOINT, "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/json"},
        {"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://collector:4318"},
        {"OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "http://collector:4318"},
    ],
)
def test_the_traces_read_agrees_with_the_fleet_trace_settings(environ: dict[str, str]) -> None:
    assert signal_exportable(TelemetrySignal.TRACES, environ) == TracingSettings.of(environ).enabled()


@pytest.mark.parametrize(("content_type", "body"), _ENCODINGS)
def test_the_recorded_metrics_decode_in_both_encodings(content_type: str, body) -> None:  # type: ignore[no-untyped-def]
    decoded = decode_metrics(body("metrics"), content_type)
    assert decoded.unsupported == 0
    points = decoded.points
    assert [point.metric_name for point in points] == ["claude_code.session.count", "claude_code.active_time.total"]
    first, second = points
    assert (first.kind, first.temporality, first.monotonic, first.scope_name) == ("sum", 1, True, _METRICS_SCOPE)
    assert (first.start_time_ns, first.time_ns, first.value) == (1791055936556000000, 1791055936709000000, 1.0)
    assert (second.unit, second.value) == ("s", 0.129)
    assert first.attributes["start_type"] == "fresh"


@pytest.mark.parametrize(("content_type", "body"), _ENCODINGS)
def test_the_recorded_logs_decode_in_both_encodings(content_type: str, body) -> None:  # type: ignore[no-untyped-def]
    records = decode_logs(body("logs"), content_type)
    assert len(records) == 6
    first = records[0]
    assert first.scope_name == _LOGS_SCOPE
    assert first.body == "claude_code.managed_settings_resolved"
    assert first.trace_flags == 1
    assert first.trace_id is not None and first.span_id is not None
    assert first.attributes["event.name"] == "managed_settings_resolved"
    assert first.attributes["event.sequence"] == 0


def _encodings(message) -> list[tuple[str, bytes]]:  # type: ignore[no-untyped-def]
    return [
        (PROTOBUF_CONTENT_TYPE, message.SerializeToString()),
        (JSON_CONTENT_TYPE, json.dumps(claude_code_telemetry.hex_ids(MessageToDict(message))).encode()),
    ]


def test_a_histogram_decodes_with_its_buckets_and_a_summary_is_counted_unsupported() -> None:
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
    from opentelemetry.proto.metrics.v1.metrics_pb2 import AggregationTemporality

    message = ExportMetricsServiceRequest()
    scope = message.resource_metrics.add().scope_metrics.add()
    scope.scope.name = _METRICS_SCOPE
    histogram = scope.metrics.add(name="latency", unit="ms")
    histogram.histogram.aggregation_temporality = AggregationTemporality.AGGREGATION_TEMPORALITY_CUMULATIVE
    point = histogram.histogram.data_points.add(count=3, sum=6.0, min=1.0, max=3.0, time_unix_nano=9)
    point.bucket_counts.extend([1, 2])
    point.explicit_bounds.append(2.0)
    summary = scope.metrics.add(name="refused").summary
    summary.data_points.add(count=1)
    summary.data_points.add(count=2)
    for content_type, body in _encodings(message):
        decoded = decode_metrics(body, content_type)
        assert decoded.unsupported == 2
        (point_decoded,) = decoded.points
        assert (point_decoded.kind, point_decoded.temporality, point_decoded.count, point_decoded.total) == (
            "histogram",
            2,
            3,
            6.0,
        )
        assert (list(point_decoded.bucket_counts), list(point_decoded.bounds)) == ([1, 2], [2.0])
        assert (point_decoded.minimum, point_decoded.maximum) == (1.0, 3.0)


def test_an_exponential_histogram_decodes_with_its_scale_and_both_sides_buckets() -> None:
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
    from opentelemetry.proto.metrics.v1.metrics_pb2 import AggregationTemporality

    message = ExportMetricsServiceRequest()
    scope = message.resource_metrics.add().scope_metrics.add()
    scope.scope.name = _METRICS_SCOPE
    exponential = scope.metrics.add(name="tokens").exponential_histogram
    exponential.aggregation_temporality = AggregationTemporality.AGGREGATION_TEMPORALITY_DELTA
    point = exponential.data_points.add(count=6, sum=12.5, scale=3, zero_count=1, min=0.0, time_unix_nano=9)
    point.positive.offset = 2
    point.positive.bucket_counts.extend([1, 3])
    point.negative.offset = -1
    point.negative.bucket_counts.append(1)
    point.exemplars.add(trace_id=b"\x01" * 16, span_id=b"\x02" * 8, as_double=1.0)
    for content_type, body in _encodings(message):
        decoded = decode_metrics(body, content_type)
        assert decoded.unsupported == 0
        (received,) = decoded.points
        assert (received.kind, received.temporality, received.count, received.total) == (
            "exponential_histogram",
            1,
            6,
            12.5,
        )
        assert (received.minimum, received.maximum) == (0.0, None)
        assert received.exponential is not None
        buckets = received.exponential
        assert (buckets.scale, buckets.zero_count) == (3, 1)
        assert (buckets.positive_offset, list(buckets.positive_counts)) == (2, [1, 3])
        assert (buckets.negative_offset, list(buckets.negative_counts)) == (-1, [1])


@pytest.mark.parametrize("decode", [decode_metrics, decode_logs])
@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (b"not json", JSON_CONTENT_TYPE),
        (b"[]", JSON_CONTENT_TYPE),
        (b"\xff\xff", PROTOBUF_CONTENT_TYPE),
        (b"{}", "text/plain"),
    ],
)
def test_a_malformed_or_unsupported_body_is_a_decode_error(decode, body: bytes, content_type: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(OtlpDecodeError):
        decode(body, content_type)


def test_rejected_counts_are_named_only_when_some_were() -> None:
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceResponse
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceResponse

    assert ExportMetricsServiceResponse.FromString(rejected_data_points(0, PROTOBUF_CONTENT_TYPE)).ByteSize() == 0
    metrics = ExportMetricsServiceResponse.FromString(rejected_data_points(3, PROTOBUF_CONTENT_TYPE))
    assert metrics.partial_success.rejected_data_points == 3
    assert json.loads(rejected_data_points(3, JSON_CONTENT_TYPE))["partialSuccess"]["rejectedDataPoints"] == "3"
    logs = ExportLogsServiceResponse.FromString(rejected_log_records(2, PROTOBUF_CONTENT_TYPE))
    assert logs.partial_success.rejected_log_records == 2
    assert "partialSuccess" not in json.loads(rejected_log_records(0, JSON_CONTENT_TYPE))


def test_admitted_data_points_are_stamped_and_replace_the_senders_stamps() -> None:
    forged = {"blizzard.lease.id": "other", "blizzard.caller": "operator", "blizzard.runner.id": "x", "user.id": "u"}
    admission = admit_data_points([_point(attributes=forged)], _lease(), _METRICS_SCOPE, runner=_RUNNER)
    (kept,) = admission.kept
    assert admission.dropped == 0
    assert kept.attributes == {
        "user.id": "u",
        "blizzard.caller": "worker",
        "blizzard.chunk.id": "ch_1",
        "blizzard.lease.id": "lease_1",
        "blizzard.runner.id": "r1",
        "blizzard.runner.name": "r-claude",
    }


def test_data_points_under_another_scope_are_dropped_and_counted() -> None:
    admission = admit_data_points(
        [_point(), _point(scope_name="some.library")], _lease(), _METRICS_SCOPE, runner=_RUNNER
    )
    assert (len(admission.kept), admission.dropped) == (1, 1)


def test_admission_caps_attributes_and_strings() -> None:
    sent = {f"k{i}": "v" * (MAX_STRING_CHARS + 5) for i in range(MAX_ATTRIBUTES + 10)}
    (kept,) = admit_data_points(
        [_point(attributes=sent, metric_name="n" * (MAX_STRING_CHARS + 1))], _lease(), _METRICS_SCOPE, runner=_RUNNER
    ).kept
    assert len(kept.metric_name) == MAX_STRING_CHARS
    assert len(kept.attributes) == MAX_ATTRIBUTES + 5
    stamps = {"worker", "ch_1", "lease_1", "r1", "r-claude"}
    assert set(kept.attributes.values()) - stamps == {"v" * MAX_STRING_CHARS}
    (record,) = admit_log_records(
        [_record(body="b" * (MAX_STRING_CHARS + 1))], _lease(), _LOGS_SCOPE, runner=_RUNNER
    ).kept
    assert record.body == "b" * MAX_STRING_CHARS


def test_a_log_record_keeps_its_trace_context_only_inside_the_leases_chunk_trace() -> None:
    inside = chunk_trace_id("ch_1")
    admission = admit_log_records(
        [
            _record(trace_id=inside, span_id=7, trace_flags=1),
            _record(trace_id=chunk_trace_id("ch_other"), span_id=7, trace_flags=1),
            _record(scope_name="some.library"),
        ],
        _lease(),
        _LOGS_SCOPE,
        runner=_RUNNER,
    )
    kept_inside, kept_outside = admission.kept
    assert admission.dropped == 1
    assert (kept_inside.trace_id, kept_inside.span_id, kept_inside.trace_flags) == (inside, 7, 1)
    assert (kept_outside.trace_id, kept_outside.span_id, kept_outside.trace_flags) == (None, None, 0)
    assert kept_inside.attributes["blizzard.runner.id"] == "r1"


@pytest.mark.parametrize(
    "config",
    [TracingConfig(), TracingConfig(harness_telemetry=True), TracingConfig(platform=True)],
)
def test_the_export_handle_is_off_unless_both_switches_are_on(config: TracingConfig) -> None:
    handle = build_received_telemetry_export(config, _ENDPOINT, resource={"service.name": "blizzard-runner"})
    assert isinstance(handle, DisabledReceivedTelemetryExport)


def test_the_export_handle_is_off_without_an_otlp_endpoint() -> None:
    config = TracingConfig(platform=True, harness_telemetry=True)
    assert isinstance(build_received_telemetry_export(config, {}, resource={}), DisabledReceivedTelemetryExport)


def test_a_url_attribute_leaves_without_its_query_string() -> None:
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter

    exporter = InMemoryLogRecordExporter()
    handle = build_received_telemetry_export(
        TracingConfig(platform=True, harness_telemetry=True), _ENDPOINT, resource={}, log_exporter=exporter
    )
    handle.forward_logs(
        [_record(attributes={"url.full": "https://x.test/p?token=secret#f", "url.query": "token=s"})], "s"
    )
    handle.shutdown(5.0)
    (readable,) = exporter.get_finished_logs()
    assert dict(readable.log_record.attributes or {}) == {"url.full": "https://x.test/p"}


@pytest.mark.parametrize("path", ["/v1/traces", "/v1/metrics", "/v1/logs", "/prefix/v1/logs/"])
def test_every_otlp_receiver_path_is_excluded_from_spans(path: str) -> None:
    assert is_excluded({"method": "POST", "path": path})


def test_other_paths_are_not_excluded() -> None:
    assert not is_excluded({"method": "POST", "path": "/api/leases"})
    assert is_excluded({"method": "POST", "path": "/api/heartbeat"})


def test_every_metric_kind_encodes_through_the_sdks_own_protobuf_encoder() -> None:
    from opentelemetry.exporter.otlp.proto.common.metrics_encoder import encode_metrics
    from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult

    class Capture(MetricExporter):
        def __init__(self) -> None:
            super().__init__()
            self.requests: list[object] = []

        def export(self, metrics_data, timeout_millis: float = 10_000, **kwargs):  # type: ignore[no-untyped-def]
            self.requests.append(encode_metrics(metrics_data))
            return MetricExportResult.SUCCESS

        def force_flush(self, timeout_millis: float = 10_000) -> bool:
            return True

        def shutdown(self, timeout_millis: float = 30_000, **kwargs: object) -> None:
            return None

    capture = Capture()
    handle = build_received_telemetry_export(
        TracingConfig(platform=True, harness_telemetry=True), _ENDPOINT, resource={}, metric_exporter=capture
    )
    handle.forward_metrics(
        [
            _point(),
            _point(metric_name="g", kind="gauge", value=1.5),
            _point(
                metric_name="h",
                kind="histogram",
                temporality=2,
                count=2,
                total=3.0,
                bucket_counts=(1, 1),
                bounds=(2.0,),
                minimum=1.0,
            ),
            _point(
                metric_name="e",
                kind="exponential_histogram",
                count=4,
                total=5.0,
                minimum=0.5,
                maximum=2.0,
                exponential=ExponentialBuckets(
                    scale=2, zero_count=1, positive_offset=3, positive_counts=(2, 1), negative_counts=()
                ),
            ),
        ],
        "svc",
    )
    handle.shutdown(5.0)
    (request,) = capture.requests
    metrics = {m.name: m for rm in request.resource_metrics for sm in rm.scope_metrics for m in sm.metrics}  # type: ignore[attr-defined]
    assert metrics["m"].sum.aggregation_temporality == 1 and metrics["m"].sum.is_monotonic
    assert metrics["g"].gauge.data_points[0].as_double == 1.5
    histogram = metrics["h"].histogram
    assert histogram.aggregation_temporality == 2
    assert (histogram.data_points[0].min, histogram.data_points[0].HasField("max")) == (1.0, False)
    exponential = metrics["e"].exponential_histogram
    assert exponential.aggregation_temporality == 1
    (exp_point,) = exponential.data_points
    assert (exp_point.scale, exp_point.zero_count, exp_point.count, exp_point.sum) == (2, 1, 4, 5.0)
    assert (exp_point.positive.offset, list(exp_point.positive.bucket_counts)) == (3, [2, 1])
    assert (exp_point.min, exp_point.max) == (0.5, 2.0)


def test_the_metrics_queue_drops_its_oldest_batch_when_full() -> None:
    import threading

    from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult, MetricsData

    from blizzard.foundation.platform_tracing.internal.received_pipeline import _MetricQueue

    release = threading.Event()
    entered = threading.Event()

    class Held(MetricExporter):
        def __init__(self) -> None:
            super().__init__()
            self.exported: list[int] = []

        def export(self, metrics_data, timeout_millis: float = 10_000, **kwargs):  # type: ignore[no-untyped-def]
            entered.set()
            release.wait(5.0)
            self.exported.append(len(metrics_data.resource_metrics))
            return MetricExportResult.SUCCESS

        def force_flush(self, timeout_millis: float = 10_000) -> bool:
            return True

        def shutdown(self, timeout_millis: float = 30_000, **kwargs: object) -> None:
            return None

    held = Held()
    queue = _MetricQueue(held, max_points=4)
    batch = {n: MetricsData(resource_metrics=[object()] * n) for n in (1, 2, 3)}  # type: ignore[list-item]
    queue.put(batch[1], 1)
    assert entered.wait(5.0)
    queue.put(batch[2], 2)
    queue.put(batch[3], 3)
    release.set()
    queue.shutdown(5.0)
    assert held.exported == [1, 3]


def test_the_metrics_queue_shutdown_discards_what_is_queued_and_leaves_an_in_flight_exporter_open() -> None:
    import threading

    from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult, MetricsData

    from blizzard.foundation.platform_tracing.internal.received_pipeline import _MetricQueue

    release = threading.Event()
    entered = threading.Event()

    class Held(MetricExporter):
        def __init__(self) -> None:
            super().__init__()
            self.exported = 0
            self.shut_down_while_exporting = False
            self.exporting = False

        def export(self, metrics_data, timeout_millis: float = 10_000, **kwargs):  # type: ignore[no-untyped-def]
            self.exporting = True
            entered.set()
            release.wait(5.0)
            self.exported += 1
            self.exporting = False
            return MetricExportResult.SUCCESS

        def force_flush(self, timeout_millis: float = 10_000) -> bool:
            return True

        def shutdown(self, timeout_millis: float = 30_000, **kwargs: object) -> None:
            self.shut_down_while_exporting = self.shut_down_while_exporting or self.exporting

    held = Held()
    queue = _MetricQueue(held)
    queue.put(MetricsData(resource_metrics=[]), 1)
    assert entered.wait(5.0)
    queue.put(MetricsData(resource_metrics=[]), 2)
    queue.shutdown(0.1)
    assert not held.shut_down_while_exporting
    release.set()
    queue._thread.join(5.0)
    assert not queue._thread.is_alive()
    assert held.exported == 1
