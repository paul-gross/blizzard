"""The admission policy for spans a worker sends the runner — what is kept, and what it says once kept.

Contract: ``blizzard-product:/delivered/tracing/platform-spans/spec/nesting.md`` §Out of the worker. A span is kept only
inside the presenting lease's step trace and the allowed scope, rebuilt to carry allowlisted attributes plus who sent
it, within the caps. Pure over :class:`ReceivedSpan`; the allowlist is a value, so widening it needs no branch here."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from blizzard.foundation.platform_tracing.attributes import CALLER, CHUNK_ID, LEASE_ID
from blizzard.foundation.platform_tracing.received import ReceivedSpan, Scalar
from blizzard.foundation.trace_ids import StepKey, trace_id
from blizzard.runner.domain.leases import LeaseRecord

__all__ = [
    "MAX_ATTRIBUTES",
    "MAX_BODY_BYTES",
    "MAX_STRING_CHARS",
    "Admission",
    "Allowlist",
    "admit",
    "attempt_trace_id",
]

#: A request body larger than this is refused before it is decoded.
MAX_BODY_BYTES = 1024 * 1024
#: Sender attributes kept per span, before the three the runner stamps.
MAX_ATTRIBUTES = 64
#: Characters kept of each string attribute value, the span name and the scope version.
MAX_STRING_CHARS = 1024

_WORKER = "worker"


@dataclass(frozen=True)
class Allowlist:
    """The one scope a span may arrive under and the attributes it may carry, each with its declared value
    type: ``string``, ``int`` or ``double``. ``None`` for either admits any scope, or any attribute, the
    caps still holding."""

    scope: str | None
    attributes: Mapping[str, str] | None


@dataclass(frozen=True)
class Admission:
    """``kept`` are the rebuilt spans to forward; ``dropped`` counts those refused."""

    kept: list[ReceivedSpan]
    dropped: int


def attempt_trace_id(lease: LeaseRecord) -> int:
    """The only trace a worker is handed: its attempt's step trace."""
    return trace_id(StepKey.attempt(lease.chunk_id, lease.epoch))


def admit(spans: list[ReceivedSpan], lease: LeaseRecord, allowlist: Allowlist) -> Admission:
    expected = attempt_trace_id(lease)
    kept = [
        _rebuilt(span, lease, allowlist)
        for span in spans
        if span.trace_id == expected and (allowlist.scope is None or span.scope_name == allowlist.scope)
    ]
    return Admission(kept=kept, dropped=len(spans) - len(kept))


def _rebuilt(span: ReceivedSpan, lease: LeaseRecord, allowlist: Allowlist) -> ReceivedSpan:
    attributes: dict[str, Scalar] = {}
    for key, value in span.attributes.items():
        if len(attributes) >= MAX_ATTRIBUTES:
            break
        if allowlist.attributes is None:
            attributes[key] = _truncated(value)
            continue
        declared = allowlist.attributes.get(key)
        if declared is not None and _is_type(value, declared):
            attributes[key] = _truncated(value)
    attributes[CALLER] = _WORKER
    attributes[CHUNK_ID] = lease.chunk_id
    attributes[LEASE_ID] = lease.lease_id
    return replace(
        span,
        name=span.name[:MAX_STRING_CHARS],
        scope_version=span.scope_version[:MAX_STRING_CHARS],
        attributes=attributes,
    )


def _is_type(value: Scalar, declared: str) -> bool:
    if declared == "string":
        return isinstance(value, str)
    if declared == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared == "double":
        return isinstance(value, float)
    return False


def _truncated(value: Scalar) -> Scalar:
    return value[:MAX_STRING_CHARS] if isinstance(value, str) else value
