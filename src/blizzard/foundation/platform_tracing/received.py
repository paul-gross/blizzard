"""A span received from outside the process, and the decoder that reads it off an OTLP/HTTP body.

The value is OpenTelemetry-free so the policy that admits it needs no SDK import; the decoder binds the
protobuf definitions only when it runs."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["JSON_CONTENT_TYPE", "PROTOBUF_CONTENT_TYPE", "OtlpDecodeError", "ReceivedSpan", "Scalar", "decode_otlp"]

JSON_CONTENT_TYPE = "application/json"
PROTOBUF_CONTENT_TYPE = "application/x-protobuf"

Scalar = str | int | float | bool


class OtlpDecodeError(ValueError):
    """The body is not a well-formed OTLP trace export in the encoding its content type names."""


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
