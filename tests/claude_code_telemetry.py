"""Claude Code's recorded OTLP export bodies — protobuf, as it sends them — and the JSON variants derived from them."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from typing_extensions import TypeIs

_DIR = Path(__file__).parent / "fixtures" / "claude_code_telemetry"
_MESSAGES = {
    "traces": ExportTraceServiceRequest,
    "metrics": ExportMetricsServiceRequest,
    "logs": ExportLogsServiceRequest,
}
_ID_KEYS = frozenset({"traceId", "spanId", "parentSpanId"})


def protobuf_body(signal: str) -> bytes:
    return (_DIR / f"{signal}.pb").read_bytes()


def json_body(signal: str) -> bytes:
    """The same export as OTLP/JSON, whose ids are hex where the protobuf JSON mapping's are base64."""
    message = _MESSAGES[signal]()
    message.ParseFromString(protobuf_body(signal))
    return json.dumps(hex_ids(MessageToDict(message))).encode()


def _string_keyed(node: object) -> TypeIs[dict[str, object]]:
    return isinstance(node, dict)


def _listed(node: object) -> TypeIs[list[object]]:
    return isinstance(node, list)


def hex_ids(node: object) -> object:
    """``node`` with each id the protobuf JSON mapping wrote as base64 rewritten as OTLP/JSON's hex."""
    if _string_keyed(node):
        return {
            key: base64.b64decode(value).hex() if key in _ID_KEYS and isinstance(value, str) else hex_ids(value)
            for key, value in node.items()
        }
    if _listed(node):
        return [hex_ids(item) for item in node]
    return node
