"""The admission policy for telemetry a worker sends the runner — what is kept, and what it says once kept.

Contract: ``docs/deployment/tracing.md`` §Worker spans. A span is kept only inside the presenting lease's work trace
and the allowed scope, rebuilt with allowlisted attributes plus who sent it; the work root's, a step root's and its
queue and claim spans' ids, and a parent of the work root, are refused. A data point or log record is kept only
under its signal's one scope, with its sender's attributes plus who sent it. Pure."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from blizzard.foundation.platform_tracing.attributes import CALLER, CHUNK_ID, LEASE_ID
from blizzard.foundation.platform_tracing.received import ReceivedDataPoint, ReceivedLogRecord, ReceivedSpan, Scalar
from blizzard.foundation.trace_ids import DerivedContext, SpanRole, StepKey, chunk_span_id, chunk_trace_id, step_root
from blizzard.runner.domain.leases import LeaseRecord
from blizzard.runner.domain.tracing.attributes import RUNNER_ID

__all__ = [
    "MAX_ATTRIBUTES",
    "MAX_BODY_BYTES",
    "MAX_STRING_CHARS",
    "Admission",
    "Allowlist",
    "admit",
    "admit_data_points",
    "admit_log_records",
    "lease_trace_id",
]

#: A request body larger than this is refused before it is decoded.
MAX_BODY_BYTES = 1024 * 1024
#: Sender attributes kept per span, data point or log record, before the ones the runner stamps.
MAX_ATTRIBUTES = 64
#: Characters kept of each string attribute value, a span, metric or unit name, a log body and the scope version.
MAX_STRING_CHARS = 1024

_WORKER = "worker"


@dataclass(frozen=True)
class Allowlist:
    """The one scope a span may arrive under and the attributes it may carry, each with its declared value
    type: ``string``, ``int`` or ``double``. ``None`` for either admits any scope, or any attribute, the
    caps still holding. ``stamp_runner`` also stamps the runner's id on each kept span."""

    scope: str | None
    attributes: Mapping[str, str] | None
    stamp_runner: bool = False


@dataclass(frozen=True)
class Admission[T]:
    """``kept`` are the rebuilt items to forward; ``dropped`` counts those refused."""

    kept: list[T]
    dropped: int


def lease_trace_id(lease: LeaseRecord) -> int:
    """The only trace a worker is handed: its chunk's."""
    return chunk_trace_id(lease.chunk_id)


def admit(spans: list[ReceivedSpan], lease: LeaseRecord, allowlist: Allowlist) -> Admission[ReceivedSpan]:
    expected = lease_trace_id(lease)
    chunk_span = chunk_span_id(lease.chunk_id)
    keys = [StepKey.attempt(lease.chunk_id, e) for e in range(lease.epoch + 1)]
    reserved = {chunk_span}
    for key in keys:
        reserved.add(step_root(key).span_id)
        reserved.update(DerivedContext.of(key, role).span_id for role in (SpanRole.QUEUE, SpanRole.CLAIM))
    kept = [
        _rebuilt(span, lease, allowlist)
        for span in spans
        if span.trace_id == expected
        and span.span_id not in reserved
        and span.parent_span_id != chunk_span
        and (allowlist.scope is None or span.scope_name == allowlist.scope)
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
    _stamp(attributes, lease)
    if allowlist.stamp_runner:
        attributes[RUNNER_ID] = lease.runner_id
    return replace(
        span,
        name=span.name[:MAX_STRING_CHARS],
        scope_version=span.scope_version[:MAX_STRING_CHARS],
        attributes=attributes,
    )


def admit_data_points(points: list[ReceivedDataPoint], lease: LeaseRecord, scope: str) -> Admission[ReceivedDataPoint]:
    """The data points under ``scope``, each rebuilt: its sender's attributes capped, then the lease's stamps —
    chunk, lease, caller and runner — replacing any the sender set."""
    kept = [
        replace(
            point,
            metric_name=point.metric_name[:MAX_STRING_CHARS],
            description=point.description[:MAX_STRING_CHARS],
            unit=point.unit[:MAX_STRING_CHARS],
            scope_version=point.scope_version[:MAX_STRING_CHARS],
            attributes=_stamped_attributes(point.attributes, lease),
        )
        for point in points
        if point.scope_name == scope
    ]
    return Admission(kept=kept, dropped=len(points) - len(kept))


def admit_log_records(records: list[ReceivedLogRecord], lease: LeaseRecord, scope: str) -> Admission[ReceivedLogRecord]:
    """The log records under ``scope``, each rebuilt as :func:`admit_data_points` does, with its body capped and
    its trace context kept only when it names the lease's work trace."""
    expected = lease_trace_id(lease)
    kept = [
        _log_rebuilt(record, lease, in_trace=record.trace_id == expected)
        for record in records
        if record.scope_name == scope
    ]
    return Admission(kept=kept, dropped=len(records) - len(kept))


def _log_rebuilt(record: ReceivedLogRecord, lease: LeaseRecord, *, in_trace: bool) -> ReceivedLogRecord:
    return replace(
        record,
        body=_truncated(record.body) if record.body is not None else None,
        severity_text=record.severity_text[:MAX_STRING_CHARS],
        scope_version=record.scope_version[:MAX_STRING_CHARS],
        trace_id=record.trace_id if in_trace else None,
        span_id=record.span_id if in_trace else None,
        trace_flags=record.trace_flags if in_trace else 0,
        attributes=_stamped_attributes(record.attributes, lease),
    )


def _stamped_attributes(sent: Mapping[str, Scalar], lease: LeaseRecord) -> dict[str, Scalar]:
    attributes = {key: _truncated(value) for key, value in list(sent.items())[:MAX_ATTRIBUTES]}
    _stamp(attributes, lease)
    attributes[RUNNER_ID] = lease.runner_id
    return attributes


def _stamp(attributes: dict[str, Scalar], lease: LeaseRecord) -> None:
    attributes[CALLER] = _WORKER
    attributes[CHUNK_ID] = lease.chunk_id
    attributes[LEASE_ID] = lease.lease_id


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
