"""The span a worker CLI command records, and how it is sent — stdlib only.

A command is a short-lived process, so this module imports neither ``httpx`` nor
``opentelemetry``: the span is a plain record, encoded by hand as OTLP/JSON, and posted through a
client the caller hands in. Contract: ``blizzard-product:/plans/tracing/platform-spans/spec/instrumentation.md``
§The CLI never pays for it."""

from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from blizzard.foundation.trace_ids import DerivedContext, format_traceparent

SCOPE_NAME = "blizzard.cli"
SCOPE_VERSION = "1"

ATTR_COMMAND = "blizzard.cli.command"
ATTR_EXIT_CODE = "process.exit.code"
ATTR_LEASE_ID = "blizzard.lease.id"
ATTR_CHUNK_ID = "blizzard.chunk.id"

SERVICE_NAME = "blizzard-cli"

TRACES_PATH = "/v1/traces"
ENV_TRACE_DEBUG = "BLIZZARD_TRACE_DEBUG"
# The whole send — connect, write and read — may take no longer than this.
SEND_CAP_SECONDS = 0.1

_KIND_INTERNAL = 1
_STATUS_ERROR = 2


class Poster(Protocol):
    """The slice of an HTTP client a send needs."""

    def post(self, url: str, *, content: bytes, headers: Mapping[str, str], timeout: float) -> object: ...


@dataclass(frozen=True)
class Clock:
    """Epoch nanoseconds from one wall-clock anchor plus monotonic deltas, so a span's duration
    never goes negative when the wall clock steps. Both sources are injectable."""

    wall_ns: Callable[[], int] = time.time_ns
    monotonic_ns: Callable[[], int] = time.monotonic_ns
    _anchor: tuple[int, int] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_anchor", (self.wall_ns(), self.monotonic_ns()))

    def now_ns(self) -> int:
        wall, monotonic = self._anchor
        return wall + (self.monotonic_ns() - monotonic)


def _random_span_id() -> int:
    return secrets.randbits(64) or 1


@dataclass
class CliSpan:
    """One worker command's span: a child of the step's context, closed with the exit code."""

    parent: DerivedContext
    span_id: int
    command: str
    chunk_id: str
    lease_id: str
    clock: Clock
    start_ns: int
    end_ns: int | None = None
    exit_code: int = 0

    @classmethod
    def open(
        cls,
        parent: DerivedContext,
        command: str,
        *,
        chunk_id: str = "",
        lease_id: str = "",
        clock: Clock | None = None,
        new_span_id: Callable[[], int] = _random_span_id,
    ) -> CliSpan:
        clock = clock or Clock()
        return cls(parent, new_span_id(), command, chunk_id, lease_id, clock, clock.now_ns())

    @property
    def traceparent(self) -> str:
        """The header that makes the runner's server span this span's child."""
        return format_traceparent(self.parent.trace_id, self.span_id, self.parent.trace_flags)

    def finish(self, exit_code: int) -> None:
        self.exit_code = exit_code
        self.end_ns = self.clock.now_ns()

    def payload(self) -> dict[str, Any]:
        """The span as an OTLP/JSON ``ExportTraceServiceRequest`` body: hex ids, string-encoded
        epoch nanoseconds, and no attribute that could carry an argument or a message."""
        attributes = {ATTR_COMMAND: self.command}
        if self.chunk_id:
            attributes[ATTR_CHUNK_ID] = self.chunk_id
        if self.lease_id:
            attributes[ATTR_LEASE_ID] = self.lease_id
        encoded = [_string(key, value) for key, value in attributes.items()]
        encoded.append({"key": ATTR_EXIT_CODE, "value": {"intValue": str(self.exit_code)}})
        span: dict[str, Any] = {
            "traceId": f"{self.parent.trace_id:032x}",
            "spanId": f"{self.span_id:016x}",
            "parentSpanId": f"{self.parent.span_id:016x}",
            "name": self.command,
            "kind": _KIND_INTERNAL,
            "startTimeUnixNano": str(self.start_ns),
            "endTimeUnixNano": str(self.end_ns if self.end_ns is not None else self.start_ns),
            "attributes": encoded,
            "flags": self.parent.trace_flags,
        }
        if self.exit_code != 0:
            span["status"] = {"code": _STATUS_ERROR}
        return {
            "resourceSpans": [
                {
                    "resource": {"attributes": [_string("service.name", SERVICE_NAME)]},
                    "scopeSpans": [{"scope": {"name": SCOPE_NAME, "version": SCOPE_VERSION}, "spans": [span]}],
                }
            ]
        }


def _string(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


def send(
    client: Poster,
    runner_url: str,
    span: CliSpan,
    *,
    headers: Mapping[str, str],
    cap: float = SEND_CAP_SECONDS,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Post the span to the runner, abandoning the daemon-thread post ``cap`` seconds in — a total
    deadline. Failures are swallowed and reach stderr only under ``BLIZZARD_TRACE_DEBUG``.
    Returns whether the post finished, so the caller knows the client is free to close."""
    environ = os.environ if environ is None else environ
    failure: list[str] = []

    def post() -> None:
        try:
            body = json.dumps(span.payload(), separators=(",", ":")).encode("utf-8")
            response = client.post(
                f"{runner_url.rstrip('/')}{TRACES_PATH}",
                content=body,
                headers={"Content-Type": "application/json", **headers},
                timeout=cap,
            )
            status = getattr(response, "status_code", 200)
            if isinstance(status, int) and status >= 400:
                failure.append(f"the runner answered {status}")
        except Exception as exc:
            failure.append(f"{type(exc).__name__}: {exc}")

    sender = threading.Thread(target=post, name="blizzard-cli-span", daemon=True)
    sender.start()
    sender.join(cap)
    finished = not sender.is_alive()
    if not finished:
        failure.append(f"no answer within {cap * 1000:.0f} ms")
    if failure and environ.get(ENV_TRACE_DEBUG):
        print(f"blizzard: trace send failed ({failure[0]})", file=sys.stderr)
    return finished
