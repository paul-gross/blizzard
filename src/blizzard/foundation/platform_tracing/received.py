"""Telemetry received from outside the process — a span, a metric data point, a log record — and the decoders
that read each off an OTLP/HTTP body.

The values are OpenTelemetry-free so the policy that admits them needs no SDK import; the decoders bind the
protobuf definitions only when they run."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from blizzard.foundation.roles import domain_model

__all__ = [
    "JSON_CONTENT_TYPE",
    "PROTOBUF_CONTENT_TYPE",
    "ExponentialBuckets",
    "MetricKind",
    "OtlpDecodeError",
    "ReceivedDataPoint",
    "ReceivedLogRecord",
    "ReceivedMetrics",
    "ReceivedSpan",
    "Scalar",
    "decode_logs",
    "decode_metrics",
    "decode_otlp",
    "encode_export_response",
    "rejected_data_points",
    "rejected_log_records",
]

JSON_CONTENT_TYPE = "application/json"
PROTOBUF_CONTENT_TYPE = "application/x-protobuf"

Scalar = str | int | float | bool


MetricKind = Literal["sum", "gauge", "histogram", "exponential_histogram"]


class OtlpDecodeError(ValueError):
    """The body is not a well-formed OTLP export in the encoding its content type names."""


@domain_model
@dataclass(frozen=True)
class ReceivedSpan:
    """One decoded span. ``kind`` and ``status_code`` are OTLP's own numbers; ``parent_span_id`` is ``None``
    for a root. Only scalar attribute values are carried."""

    trace_id: int
    span_id: int
    parent_span_id: int | None
    name: str
    kind: int
    start_time_ns: int
    end_time_ns: int
    status_code: int
    scope_name: str
    scope_version: str
    attributes: dict[str, Scalar] = field(default_factory=dict)


def decode_otlp(body: bytes, content_type: str) -> list[ReceivedSpan]:
    """Every span in ``body``; ``content_type`` is ``application/json`` or ``application/x-protobuf``."""
    from blizzard.foundation.platform_tracing.internal.otlp_decode import decode

    return decode(body, content_type)


def encode_export_response(rejected_spans: int, content_type: str) -> bytes:
    """An ``ExportTraceServiceResponse`` in the request's encoding, naming ``rejected_spans`` only when some were."""
    from blizzard.foundation.platform_tracing.internal.otlp_decode import encode_response

    return encode_response(rejected_spans, content_type)


@domain_model
@dataclass(frozen=True)
class ExponentialBuckets:
    """An exponential histogram point's buckets: its ``scale``, the count at zero, and each side's offset and
    counts."""

    scale: int
    zero_count: int
    positive_offset: int = 0
    positive_counts: Sequence[int] = ()
    negative_offset: int = 0
    negative_counts: Sequence[int] = ()


@domain_model
@dataclass(frozen=True)
class ReceivedDataPoint:
    """One decoded metric data point with its metric. ``temporality`` is OTLP's own number (1 delta, 2 cumulative);
    ``monotonic`` matters for a sum only. A number point carries ``value``; a histogram point ``count``, ``total``,
    ``bucket_counts``, ``bounds`` and, when sent, ``minimum`` and ``maximum``, or ``exponential`` in place of the
    buckets. Only scalar attribute values are carried."""

    metric_name: str
    description: str
    unit: str
    kind: MetricKind
    temporality: int
    monotonic: bool
    scope_name: str
    scope_version: str
    start_time_ns: int
    time_ns: int
    value: int | float = 0
    count: int = 0
    total: float = 0.0
    bucket_counts: Sequence[int] = ()
    bounds: Sequence[float] = ()
    minimum: float | None = None
    maximum: float | None = None
    exponential: ExponentialBuckets | None = None
    attributes: dict[str, Scalar] = field(default_factory=dict)


@domain_model
@dataclass(frozen=True)
class ReceivedMetrics:
    """A decoded metrics export: its ``points``, and how many points it carried of a kind the runner cannot
    forward (a summary) — refused, never silently lost."""

    points: list[ReceivedDataPoint]
    unsupported: int = 0


@domain_model
@dataclass(frozen=True)
class ReceivedLogRecord:
    """One decoded log record. ``trace_id`` and ``span_id`` are ``None`` when the record names no trace; a
    ``body`` that is not a scalar reads as ``None``. Only scalar attribute values are carried."""

    time_ns: int
    observed_time_ns: int
    severity_number: int
    severity_text: str
    body: Scalar | None
    trace_id: int | None
    span_id: int | None
    trace_flags: int
    scope_name: str
    scope_version: str
    attributes: dict[str, Scalar] = field(default_factory=dict)


def decode_metrics(body: bytes, content_type: str) -> ReceivedMetrics:
    """Every sum, gauge, histogram and exponential histogram data point in ``body``, and the count of summary
    points, which the SDK's exporters cannot carry."""
    from blizzard.foundation.platform_tracing.internal.otlp_decode import decode_metric_points

    return decode_metric_points(body, content_type)


def decode_logs(body: bytes, content_type: str) -> list[ReceivedLogRecord]:
    """Every log record in ``body``."""
    from blizzard.foundation.platform_tracing.internal.otlp_decode import decode_log_records

    return decode_log_records(body, content_type)


def rejected_data_points(rejected: int, content_type: str) -> bytes:
    """An ``ExportMetricsServiceResponse`` in the request's encoding, naming ``rejected`` only when some were."""
    from blizzard.foundation.platform_tracing.internal.otlp_decode import encode_metrics_response

    return encode_metrics_response(rejected, content_type)


def rejected_log_records(rejected: int, content_type: str) -> bytes:
    """An ``ExportLogsServiceResponse`` in the request's encoding, naming ``rejected`` only when some were."""
    from blizzard.foundation.platform_tracing.internal.otlp_decode import encode_logs_response

    return encode_logs_response(rejected, content_type)
