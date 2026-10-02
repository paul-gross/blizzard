"""The platform attribute both daemons stamp, and the helper that stamps them on the request's span.

Contract: ``blizzard-product:/plans/tracing/platform-spans/spec/instrumentation.md`` §Attributes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from opentelemetry import trace

from blizzard.foundation.trace_spans import Attributes

CALLER = "blizzard.caller"
CHUNK_ID = "blizzard.chunk.id"
LEASE_ID = "blizzard.lease.id"

#: The CLI's instrumentation scope — the one scope a worker's span may arrive under.
CLI_SCOPE = "blizzard.cli"
CLI_COMMAND = "blizzard.cli.command"
EXIT_CODE = "process.exit.code"

#: The attributes a CLI span may carry, each with its declared value type (``string``, ``int`` or ``double``).
CLI_ATTRIBUTES: Mapping[str, str] = {
    CLI_COMMAND: "string",
    EXIT_CODE: "int",
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
