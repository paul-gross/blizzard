"""OTLP/HTTP export bodies — traces, metrics, logs — to the received values, both encodings through one protobuf
message per signal."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

from google.protobuf.json_format import MessageToJson, ParseDict, ParseError
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest, ExportLogsServiceResponse
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
    ExportMetricsServiceResponse,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue
from opentelemetry.proto.logs.v1.logs_pb2 import LogRecord
from opentelemetry.proto.metrics.v1.metrics_pb2 import ExponentialHistogramDataPoint, Metric, NumberDataPoint
from opentelemetry.proto.trace.v1.trace_pb2 import Span

from blizzard.foundation.platform_tracing.received import (
    JSON_CONTENT_TYPE,
    PROTOBUF_CONTENT_TYPE,
    ExponentialBuckets,
    MetricKind,
    OtlpDecodeError,
    ReceivedDataPoint,
    ReceivedLogRecord,
    ReceivedMetrics,
    ReceivedSpan,
    Scalar,
)

_ID_FIELDS = ("traceId", "spanId", "parentSpanId")
_TRACE_ID_BYTES = 16
_SPAN_ID_BYTES = 8


def decode(body: bytes, content_type: str) -> list[ReceivedSpan]:
    message = ExportTraceServiceRequest()
    _parse(body, content_type, message, _normalize_ids)
    return _spans(message)


def decode_metric_points(body: bytes, content_type: str) -> ReceivedMetrics:
    message = ExportMetricsServiceRequest()
    _parse(body, content_type, message, _normalize_metrics)
    metrics = [
        (metric, scope.scope.name, scope.scope.version)
        for resource in message.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    ]
    return ReceivedMetrics(
        points=[point for metric, name, version in metrics for point in _points(metric, name, version)],
        unsupported=sum(len(metric.summary.data_points) for metric, _, _ in metrics if metric.HasField("summary")),
    )


def decode_log_records(body: bytes, content_type: str) -> list[ReceivedLogRecord]:
    message = ExportLogsServiceRequest()
    _parse(body, content_type, message, _normalize_log_ids)
    return [
        _log_record(record, scope.scope.name, scope.scope.version)
        for resource in message.resource_logs
        for scope in resource.scope_logs
        for record in scope.log_records
    ]


def encode_response(rejected_spans: int, content_type: str) -> bytes:
    response = ExportTraceServiceResponse()
    if rejected_spans:
        response.partial_success.rejected_spans = rejected_spans
    return _encoded(response, content_type)


def encode_metrics_response(rejected: int, content_type: str) -> bytes:
    response = ExportMetricsServiceResponse()
    if rejected:
        response.partial_success.rejected_data_points = rejected
    return _encoded(response, content_type)


def encode_logs_response(rejected: int, content_type: str) -> bytes:
    response = ExportLogsServiceResponse()
    if rejected:
        response.partial_success.rejected_log_records = rejected
    return _encoded(response, content_type)


def _encoded(response: Any, content_type: str) -> bytes:
    if content_type == PROTOBUF_CONTENT_TYPE:
        return response.SerializeToString()
    return MessageToJson(response, indent=None).encode()


def _parse(body: bytes, content_type: str, message: Any, normalize: Callable[[Any], None]) -> None:
    if content_type == PROTOBUF_CONTENT_TYPE:
        try:
            message.ParseFromString(body)
        except DecodeError as exc:
            raise OtlpDecodeError("malformed protobuf body") from exc
    elif content_type == JSON_CONTENT_TYPE:
        _parse_json(body, message, normalize)
    else:
        raise OtlpDecodeError(f"unsupported content type {content_type!r}")


def _parse_json(body: bytes, message: Any, normalize: Callable[[Any], None]) -> None:
    try:
        document = json.loads(body)
        normalize(document)
        ParseDict(document, message, ignore_unknown_fields=True)
    except (ValueError, TypeError, AttributeError, KeyError, RecursionError, ParseError) as exc:
        raise OtlpDecodeError("malformed JSON body") from exc


def _normalize_ids(document: Any) -> None:
    """OTLP/JSON carries ids as hex; the protobuf JSON mapping reads bytes as base64."""
    if not isinstance(document, dict):
        raise ValueError("the body is not an object")
    for resource in document.get("resourceSpans") or []:
        for scope in resource.get("scopeSpans") or []:
            for span in scope.get("spans") or []:
                for name in _ID_FIELDS:
                    value = span.get(name)
                    if value:
                        span[name] = base64.b64encode(bytes.fromhex(value)).decode("ascii")
                    elif name in span:
                        del span[name]


def _normalize_log_ids(document: Any) -> None:
    if not isinstance(document, dict):
        raise ValueError("the body is not an object")
    for resource in document.get("resourceLogs") or []:
        for scope in resource.get("scopeLogs") or []:
            for record in scope.get("logRecords") or []:
                for name in ("traceId", "spanId"):
                    value = record.get(name)
                    if value:
                        record[name] = base64.b64encode(bytes.fromhex(value)).decode("ascii")
                    elif name in record:
                        del record[name]


def _normalize_metrics(document: Any) -> None:
    """Exemplars are never read, and their hex ids would not parse as the protobuf JSON mapping's base64."""
    if not isinstance(document, dict):
        raise ValueError("the body is not an object")
    for resource in document.get("resourceMetrics") or []:
        for scope in resource.get("scopeMetrics") or []:
            for metric in scope.get("metrics") or []:
                for kind in ("sum", "gauge", "histogram", "exponentialHistogram"):
                    for point in (metric.get(kind) or {}).get("dataPoints") or []:
                        point.pop("exemplars", None)


def _spans(message: ExportTraceServiceRequest) -> list[ReceivedSpan]:
    received: list[ReceivedSpan] = []
    for resource in message.resource_spans:
        for scope in resource.scope_spans:
            for span in scope.spans:
                received.append(_received(span, scope.scope.name, scope.scope.version))
    return received


def _received(span: Span, scope_name: str, scope_version: str) -> ReceivedSpan:
    if len(span.trace_id) != _TRACE_ID_BYTES or len(span.span_id) != _SPAN_ID_BYTES:
        raise OtlpDecodeError("a span carries a malformed id")
    if span.parent_span_id and len(span.parent_span_id) != _SPAN_ID_BYTES:
        raise OtlpDecodeError("a span carries a malformed parent id")
    return ReceivedSpan(
        trace_id=int.from_bytes(span.trace_id, "big"),
        span_id=int.from_bytes(span.span_id, "big"),
        parent_span_id=int.from_bytes(span.parent_span_id, "big") if span.parent_span_id else None,
        name=span.name,
        kind=int(span.kind),
        start_time_ns=int(span.start_time_unix_nano),
        end_time_ns=int(span.end_time_unix_nano),
        status_code=int(span.status.code),
        scope_name=scope_name,
        scope_version=scope_version,
        attributes=_scalar_pairs(span.attributes),
    )


def _scalar(value: AnyValue) -> Scalar | None:
    kind = value.WhichOneof("value")
    if kind == "string_value":
        return value.string_value
    if kind == "bool_value":
        return value.bool_value
    if kind == "int_value":
        return value.int_value
    if kind == "double_value":
        return value.double_value
    return None


_KINDS: tuple[MetricKind, ...] = ("sum", "gauge", "histogram", "exponential_histogram")


def _points(metric: Metric, scope_name: str, scope_version: str) -> list[ReceivedDataPoint]:
    kind = metric.WhichOneof("data")
    if kind not in _KINDS:
        return []
    data = getattr(metric, kind)
    temporality = int(getattr(data, "aggregation_temporality", 0))
    monotonic = bool(getattr(data, "is_monotonic", False))
    common = {
        "metric_name": metric.name,
        "description": metric.description,
        "unit": metric.unit,
        "kind": kind,
        "temporality": temporality,
        "monotonic": monotonic,
        "scope_name": scope_name,
        "scope_version": scope_version,
    }
    points: list[ReceivedDataPoint] = []
    for point in data.data_points:
        shared = {
            **common,
            "start_time_ns": int(point.start_time_unix_nano),
            "time_ns": int(point.time_unix_nano),
            "attributes": _scalar_pairs(point.attributes),
        }
        if kind == "exponential_histogram":
            points.append(_exponential(point, shared))
        elif kind == "histogram":
            points.append(
                ReceivedDataPoint(
                    **shared,  # pyright: ignore[reportArgumentType]
                    count=int(point.count),
                    total=float(point.sum) if point.HasField("sum") else 0.0,
                    bucket_counts=tuple(point.bucket_counts),
                    bounds=tuple(point.explicit_bounds),
                    minimum=float(point.min) if point.HasField("min") else None,
                    maximum=float(point.max) if point.HasField("max") else None,
                )
            )
        else:
            points.append(ReceivedDataPoint(**shared, value=_number(point)))  # pyright: ignore[reportArgumentType]
    return points


def _exponential(point: ExponentialHistogramDataPoint, shared: dict[str, Any]) -> ReceivedDataPoint:
    return ReceivedDataPoint(
        **shared,
        count=int(point.count),
        total=float(point.sum) if point.HasField("sum") else 0.0,
        minimum=float(point.min) if point.HasField("min") else None,
        maximum=float(point.max) if point.HasField("max") else None,
        exponential=ExponentialBuckets(
            scale=int(point.scale),
            zero_count=int(point.zero_count),
            positive_offset=int(point.positive.offset),
            positive_counts=tuple(point.positive.bucket_counts),
            negative_offset=int(point.negative.offset),
            negative_counts=tuple(point.negative.bucket_counts),
        ),
    )


def _number(point: NumberDataPoint) -> int | float:
    return int(point.as_int) if point.WhichOneof("value") == "as_int" else float(point.as_double)


def _log_record(record: LogRecord, scope_name: str, scope_version: str) -> ReceivedLogRecord:
    if record.trace_id and len(record.trace_id) != _TRACE_ID_BYTES:
        raise OtlpDecodeError("a log record carries a malformed trace id")
    if record.span_id and len(record.span_id) != _SPAN_ID_BYTES:
        raise OtlpDecodeError("a log record carries a malformed span id")
    return ReceivedLogRecord(
        time_ns=int(record.time_unix_nano),
        observed_time_ns=int(record.observed_time_unix_nano),
        severity_number=int(record.severity_number),
        severity_text=record.severity_text,
        body=_scalar(record.body),
        trace_id=int.from_bytes(record.trace_id, "big") if record.trace_id else None,
        span_id=int.from_bytes(record.span_id, "big") if record.span_id else None,
        trace_flags=int(record.flags) & 0xFF,
        scope_name=scope_name,
        scope_version=scope_version,
        attributes=_scalar_pairs(record.attributes),
    )


def _scalar_pairs(pairs: Any) -> dict[str, Scalar]:
    attributes: dict[str, Scalar] = {}
    for pair in pairs:
        value = _scalar(pair.value)
        if value is not None:
            attributes[pair.key] = value
    return attributes
