"""OTLP/HTTP trace export bodies to :class:`ReceivedSpan`s — both encodings through one protobuf message."""

from __future__ import annotations

import base64
import json
from typing import Any

from google.protobuf.json_format import ParseDict
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span

from blizzard.foundation.platform_tracing.received import (
    JSON_CONTENT_TYPE,
    PROTOBUF_CONTENT_TYPE,
    OtlpDecodeError,
    ReceivedSpan,
    Scalar,
)

_ID_FIELDS = ("traceId", "spanId", "parentSpanId")
_TRACE_ID_BYTES = 16
_SPAN_ID_BYTES = 8


def decode(body: bytes, content_type: str) -> list[ReceivedSpan]:
    message = ExportTraceServiceRequest()
    if content_type == PROTOBUF_CONTENT_TYPE:
        try:
            message.ParseFromString(body)
        except DecodeError as exc:
            raise OtlpDecodeError("malformed protobuf body") from exc
    elif content_type == JSON_CONTENT_TYPE:
        _parse_json(body, message)
    else:
        raise OtlpDecodeError(f"unsupported content type {content_type!r}")
    return _spans(message)


def _parse_json(body: bytes, message: ExportTraceServiceRequest) -> None:
    try:
        document = json.loads(body)
        _normalize_ids(document)
        ParseDict(document, message, ignore_unknown_fields=True)
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
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
        attributes=_scalars(span),
    )


def _scalars(span: Span) -> dict[str, Scalar]:
    attributes: dict[str, Scalar] = {}
    for pair in span.attributes:
        value = _scalar(pair.value)
        if value is not None:
            attributes[pair.key] = value
    return attributes


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


