"""The platform attribute both daemons stamp, and the helper that stamps them on the request's span.

Contract: ``blizzard-product:/delivered/tracing/platform-spans/spec/instrumentation.md`` §Attributes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from opentelemetry import trace

from blizzard.foundation import cli_spans
from blizzard.foundation.trace_spans import Attributes

CALLER = "blizzard.caller"

#: The attributes a CLI span may carry, each with its declared value type (``string``, ``int`` or ``double``).
CLI_ATTRIBUTES: Mapping[str, str] = {
    cli_spans.ATTR_COMMAND: "string",
    cli_spans.ATTR_EXIT_CODE: "int",
    "http.request.method": "string",
    "http.response.status_code": "int",
    "url.full": "string",
    "server.address": "string",
    "server.port": "int",
    "error.type": "string",
}

#: Who a verified credential names; stamped only where the credential is verified.
Caller = Literal["runner", "board", "operator", "worker"]


def annotate(attributes: Attributes) -> None:
    """Set ``attributes`` on the current span; a no-op when none is recording."""
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attributes(dict(attributes))


def annotate_caller(caller: Caller) -> None:
    annotate({CALLER: caller})
