"""The span receiver's pure parts (unit tier) — decoding both OTLP encodings, admission, the rate bucket, forwarding."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.platform_tracing.attributes import CLI_ATTRIBUTES, CLI_SCOPE
from blizzard.foundation.platform_tracing.handle import DisabledPlatformTracing, build_platform_tracing
from blizzard.foundation.platform_tracing.received import (
    JSON_CONTENT_TYPE,
    PROTOBUF_CONTENT_TYPE,
    OtlpDecodeError,
    ReceivedSpan,
    decode_otlp,
)
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_ids import StepKey, trace_id
from blizzard.runner.domain.leases import LeaseRecord
from blizzard.runner.domain.tracing.receiver import MAX_ATTRIBUTES, MAX_STRING_CHARS, Allowlist, admit
from blizzard.runner.domain.tracing.receiver_limits import (
    BUCKET_CAPACITY,
    ReceiverCounter,
    SpanRateLimiter,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)
_ALLOWLIST = Allowlist(scope=CLI_SCOPE, attributes=CLI_ATTRIBUTES)
_SPAN_ID = 0x00F067AA0BA902B7


def _lease(chunk_id: str = "ch_1", epoch: int = 1) -> LeaseRecord:
    return LeaseRecord(
        lease_id="lease_1",
        chunk_id=chunk_id,
        graph_id="gr_1",
        node_id="nd_build",
        node_name="build",
        epoch=epoch,
        runner_id="r1",
        retries_max=2,
        created_at=_NOW,
    )


def _trace(chunk_id: str = "ch_1", epoch: int = 1) -> int:
    return trace_id(StepKey.attempt(chunk_id, epoch))


def _span(**changes: object) -> ReceivedSpan:
    fields: dict[str, object] = {
        "trace_id": _trace(),
        "span_id": _SPAN_ID,
        "parent_span_id": None,
        "name": "blizzard artifact create",
        "kind": 1,
        "start_time_ns": 1_000,
        "end_time_ns": 2_000,
        "status_code": 1,
        "scope_name": CLI_SCOPE,
        "scope_version": "1",
        "attributes": {"blizzard.cli.command": "artifact create", "process.exit.code": 0},
    }
    fields.update(changes)
    return ReceivedSpan(**fields)  # type: ignore[arg-type]


def _otlp_json(trace: int, *, parent: int | None = _SPAN_ID) -> bytes:
    span: dict[str, object] = {
        "traceId": f"{trace:032x}",
        "spanId": f"{_SPAN_ID:016x}",
        "name": "cli",
        "kind": 3,
        "startTimeUnixNano": "1000",
        "endTimeUnixNano": "2000",
        "attributes": [
            {"key": "blizzard.cli.command", "value": {"stringValue": "artifact create"}},
            {"key": "process.exit.code", "value": {"intValue": "0"}},
            {"key": "ratio", "value": {"doubleValue": 0.5}},
            {"key": "flag", "value": {"boolValue": True}},
            {"key": "nested", "value": {"arrayValue": {"values": []}}},
        ],
        "status": {"code": 2, "message": "dropped"},
    }
    if parent is not None:
        span["parentSpanId"] = f"{parent:016x}"
    document = {"resourceSpans": [{"scopeSpans": [{"scope": {"name": CLI_SCOPE, "version": "1"}, "spans": [span]}]}]}
    return json.dumps(document).encode()


def _otlp_protobuf(body: bytes) -> bytes:
    from google.protobuf.json_format import Parse

    return Parse(body, ExportTraceServiceRequest(), ignore_unknown_fields=True).SerializeToString()


def _json_with_binary_ids(trace: int) -> bytes:
    import base64

    document = json.loads(_otlp_json(trace))
    span = document["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    for key in ("traceId", "spanId", "parentSpanId"):
        span[key] = base64.b64encode(bytes.fromhex(span[key])).decode()
    return json.dumps(document).encode()


def test_both_encodings_decode_to_the_same_spans_with_hex_ids_round_tripped() -> None:
    body = _otlp_json(_trace())
    from_json = decode_otlp(body, JSON_CONTENT_TYPE)
    from_protobuf = decode_otlp(_otlp_protobuf(_json_with_binary_ids(_trace())), PROTOBUF_CONTENT_TYPE)
    assert from_json == from_protobuf
    (span,) = from_json
    assert (span.trace_id, span.span_id, span.parent_span_id) == (_trace(), _SPAN_ID, _SPAN_ID)
    assert (span.kind, span.status_code, span.start_time_ns, span.end_time_ns) == (3, 2, 1000, 2000)
    assert (span.scope_name, span.scope_version) == (CLI_SCOPE, "1")
    assert span.attributes == {
        "blizzard.cli.command": "artifact create",
        "process.exit.code": 0,
        "ratio": 0.5,
        "flag": True,
    }


def test_a_root_span_has_no_parent() -> None:
    (span,) = decode_otlp(_otlp_json(_trace(), parent=None), JSON_CONTENT_TYPE)
    assert span.parent_span_id is None


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (b"not json", JSON_CONTENT_TYPE),
        (b"[]", JSON_CONTENT_TYPE),
        (b'{"resourceSpans": [{"scopeSpans": [{"spans": [{"traceId": "zz"}]}]}]}', JSON_CONTENT_TYPE),
        (b'{"resourceSpans": [{"scopeSpans": [{"spans": [{"traceId": "ab", "spanId": "cd"}]}]}]}', JSON_CONTENT_TYPE),
        (b"\xff\xff\xff", PROTOBUF_CONTENT_TYPE),
        (b"{}", "text/plain"),
    ],
)
def test_a_malformed_body_is_a_typed_decode_error(body: bytes, content_type: str) -> None:
    with pytest.raises(OtlpDecodeError):
        decode_otlp(body, content_type)


def _json_span(**fields: object) -> bytes:
    span = {"traceId": f"{_trace():032x}", "spanId": f"{_SPAN_ID:016x}", **fields}
    return json.dumps({"resourceSpans": [{"scopeSpans": [{"spans": [span]}]}]}).encode()


@pytest.mark.parametrize(
    "body",
    [
        _json_span(name={"a": 1}),
        _json_span(startTimeUnixNano="-1"),
        _json_span(startTimeUnixNano="99999999999999999999999"),
        _json_span(attributes=[{"key": "k", "value": {"intValue": "x"}}]),
        _json_span(traceId="ab" * 15),
        _json_span(spanId="ab" * 7),
        _json_span(parentSpanId="ab" * 7),
        b"[" * 100000,
        b'{"resourceSpans": [1]}',
        b'{"resourceSpans": [{"scopeSpans": [{"spans": [5]}]}]}',
    ],
)
def test_every_malformed_json_shape_is_a_decode_error(body: bytes) -> None:
    with pytest.raises(OtlpDecodeError):
        decode_otlp(body, JSON_CONTENT_TYPE)


def test_empty_hex_ids_are_a_decode_error_and_an_empty_parent_is_a_root() -> None:
    with pytest.raises(OtlpDecodeError):
        decode_otlp(_json_span(traceId=""), JSON_CONTENT_TYPE)
    (span,) = decode_otlp(_json_span(parentSpanId=""), JSON_CONTENT_TYPE)
    assert span.parent_span_id is None
    assert decode_otlp(b"{}", JSON_CONTENT_TYPE) == []


def test_a_protobuf_span_with_a_short_id_is_a_decode_error() -> None:
    message = ExportTraceServiceRequest()
    span = message.resource_spans.add().scope_spans.add().spans.add()
    span.trace_id = b"\x01" * 15
    span.span_id = b"\x01" * 8
    with pytest.raises(OtlpDecodeError):
        decode_otlp(message.SerializeToString(), PROTOBUF_CONTENT_TYPE)


def test_admit_checks_each_declared_value_type() -> None:
    allowlist = Allowlist(scope=CLI_SCOPE, attributes={"i": "int", "d": "double", "s": "string", "u": "other"})
    attributes = {"i": True, "d": 1, "s": 1, "u": "x"}
    (kept,) = admit([_span(attributes=attributes)], _lease(), allowlist).kept
    assert set(kept.attributes) == {"blizzard.caller", "blizzard.chunk.id", "blizzard.lease.id"}
    good = {"i": 3, "d": 1.5, "s": "x"}
    (kept,) = admit([_span(attributes=good)], _lease(), allowlist).kept
    assert {k: kept.attributes[k] for k in good} == good


@pytest.mark.parametrize(
    ("kind", "name"),
    [(0, "INTERNAL"), (1, "INTERNAL"), (2, "SERVER"), (3, "CLIENT"), (4, "PRODUCER"), (5, "CONSUMER"), (9, "INTERNAL")],
)
def test_forward_maps_otlp_kinds_and_statuses(kind: int, name: str) -> None:
    exporter = InMemorySpanExporter()
    handle = _handle(exporter)
    handle.forward([_span(kind=kind, status_code=2, scope_version="")], "blizzard-cli")
    handle.forward([_span(span_id=2, status_code=1)], "blizzard-cli")
    handle.forward([_span(span_id=3, status_code=7)], "blizzard-cli")
    handle.shutdown(5.0)
    first, ok, unknown = exporter.get_finished_spans()
    assert first.kind.name == name
    assert (first.status.status_code.name, ok.status.status_code.name, unknown.status.status_code.name) == (
        "ERROR",
        "OK",
        "UNSET",
    )
    assert (first.start_time, first.end_time) == (1_000, 2_000)
    assert first.parent is None
    assert first.instrumentation_scope is not None and first.instrumentation_scope.version is None
    assert ok.instrumentation_scope is not None and ok.instrumentation_scope.version == "1"


def _handle(exporter: InMemorySpanExporter):  # type: ignore[no-untyped-def]
    return build_platform_tracing(
        TracingConfig(platform=True, platform_sample_ratio=0.0),
        {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"},
        resource={"service.name": "blizzard-runner"},
        scope="s",
        scope_version="1",
        exporter=exporter,
    )


def test_admit_keeps_an_in_step_cli_span_and_drops_the_rest() -> None:
    spans = [
        _span(),
        _span(trace_id=_trace("ch_2")),
        _span(trace_id=_trace("ch_1", 2)),
        _span(scope_name="evil"),
    ]
    admission = admit(spans, _lease(), _ALLOWLIST)
    assert [s.trace_id for s in admission.kept] == [_trace()]
    assert admission.dropped == 3


def test_admit_keeps_only_allowlisted_attributes_of_their_declared_type() -> None:
    attributes = {
        "blizzard.cli.command": "x",
        "process.exit.code": "0",
        "url.full": "http://runner/api",
        "secret": "s3cret",
        "server.port": True,
    }
    (kept,) = admit([_span(attributes=attributes)], _lease(), _ALLOWLIST).kept
    assert kept.attributes == {
        "blizzard.cli.command": "x",
        "url.full": "http://runner/api",
        "blizzard.caller": "worker",
        "blizzard.chunk.id": "ch_1",
        "blizzard.lease.id": "lease_1",
    }


def test_admit_overwrites_a_planted_caller_chunk_and_lease() -> None:
    planted = {"blizzard.caller": "operator", "blizzard.chunk.id": "ch_9", "blizzard.lease.id": "lease_9"}
    allowlist = Allowlist(scope=CLI_SCOPE, attributes={**CLI_ATTRIBUTES, **dict.fromkeys(planted, "string")})
    (kept,) = admit([_span(attributes=planted)], _lease(), allowlist).kept
    assert kept.attributes == {"blizzard.caller": "worker", "blizzard.chunk.id": "ch_1", "blizzard.lease.id": "lease_1"}


def test_admit_truncates_long_strings_and_caps_the_attribute_count() -> None:
    many = {f"k{i}": "v" for i in range(MAX_ATTRIBUTES + 10)}
    allowlist = Allowlist(scope=CLI_SCOPE, attributes=dict.fromkeys(many, "string"))
    long = "x" * (MAX_STRING_CHARS + 5)
    (kept,) = admit([_span(name=long, scope_version=long, attributes=many)], _lease(), allowlist).kept
    assert len(kept.name) == len(kept.scope_version) == MAX_STRING_CHARS
    assert len(kept.attributes) == MAX_ATTRIBUTES + 3
    (clipped,) = admit([_span(attributes={"url.full": long})], _lease(), _ALLOWLIST).kept
    assert clipped.attributes["url.full"] == "x" * MAX_STRING_CHARS


def test_the_bucket_refuses_past_capacity_and_refills_on_the_injected_clock() -> None:
    clock = FixedClock(_NOW)
    limiter = SpanRateLimiter(clock)
    assert limiter.take("a", BUCKET_CAPACITY)
    assert not limiter.take("a", 1)
    assert limiter.take("b", 1)
    clock.advance(timedelta(seconds=2))
    assert limiter.take("a", 100)
    assert not limiter.take("a", 1)


def test_a_request_larger_than_the_bucket_never_fits() -> None:
    assert not SpanRateLimiter(FixedClock(_NOW)).take("a", BUCKET_CAPACITY + 1)


def test_an_idle_bucket_is_evicted() -> None:
    clock = FixedClock(_NOW)
    limiter = SpanRateLimiter(clock)
    limiter.take("a", 1)
    clock.advance(timedelta(minutes=5))
    limiter.take("b", 1)
    assert list(limiter._buckets) == ["b"]


def test_the_counter_tallies_accepted_and_dropped() -> None:
    counter = ReceiverCounter()
    counter.record(accepted=2, dropped=1)
    counter.record(accepted=1, dropped=0)
    assert (counter.count().accepted, counter.count().dropped) == (3, 1)


def test_a_forwarded_span_reaches_the_exporter_rebuilt_and_redacted() -> None:
    exporter = InMemorySpanExporter()
    handle = build_platform_tracing(
        TracingConfig(platform=True, platform_sample_ratio=0.0),
        {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"},
        resource={"service.name": "blizzard-runner"},
        scope="s",
        scope_version="1",
        exporter=exporter,
    )
    (kept,) = admit(
        [_span(parent_span_id=7, attributes={"url.full": "http://runner/api?token=abc#frag"})], _lease(), _ALLOWLIST
    ).kept
    kept.attributes["url.query"] = "token=abc"
    handle.forward([kept], "blizzard-cli")
    handle.shutdown(5.0)
    (out,) = exporter.get_finished_spans()
    assert out.resource.attributes["service.name"] == "blizzard-cli"
    assert out.context is not None and out.context.trace_id == _trace() and out.context.span_id == _SPAN_ID
    assert out.parent is not None and out.parent.span_id == 7
    assert out.instrumentation_scope is not None and out.instrumentation_scope.name == CLI_SCOPE
    assert out.attributes is not None
    assert out.attributes["url.full"] == "http://runner/api"
    assert "url.query" not in out.attributes
    assert out.attributes["blizzard.caller"] == "worker"


def test_the_disabled_handle_forwards_nothing() -> None:
    handle = DisabledPlatformTracing()
    assert not handle.enabled
    handle.forward([_span()], "blizzard-cli")
