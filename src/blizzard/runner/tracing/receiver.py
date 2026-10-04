"""The admission policy for telemetry a worker sends the runner — what is kept, and what it says once kept.

Contract: ``docs/deployment/tracing.md`` §Worker spans. A span is kept only inside the presenting lease's work trace
and the allowed scope, rebuilt with allowlisted attributes plus who sent it; the work root's, a step root's and its
queue and claim spans' ids, and a parent of the work root, are refused. A data point or log record is kept only
under its signal's one scope, with its sender's attributes plus who sent it. Pure."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from blizzard.foundation.platform_tracing.attributes import CALLER, CHUNK_ID, CLI_ATTRIBUTES, CLI_SCOPE, LEASE_ID
from blizzard.foundation.platform_tracing.received import ReceivedDataPoint, ReceivedLogRecord, ReceivedSpan, Scalar
from blizzard.foundation.roles import dto
from blizzard.foundation.trace_ids import DerivedContext, SpanRole, StepKey, chunk_span_id, chunk_trace_id, step_root
from blizzard.runner.harness.harness_telemetry_plan import (
    CLAUDE_CODE_SCOPES,
    CLAUDE_CODE_SERVICE_NAME,
    CLAUDE_CODE_TRACING_SCOPE,
)
from blizzard.runner.leases import Lease
from blizzard.runner.tracing.attributes import RUNNER_ID

__all__ = [
    "CLI_SERVICE_NAME",
    "MAX_ATTRIBUTES",
    "MAX_BODY_BYTES",
    "MAX_STRING_CHARS",
    "PROGRAM_SERVICE_NAME",
    "Admission",
    "Allowlist",
    "SpanRouting",
    "admit",
    "admit_data_points",
    "admit_log_records",
    "lease_trace_id",
    "route_spans",
    "service_name_for",
    "spans_by_service_name",
]

#: A request body larger than this is refused before it is decoded.
MAX_BODY_BYTES = 1024 * 1024
#: Sender attributes kept per span, data point or log record, before the ones the runner stamps.
MAX_ATTRIBUTES = 64
#: Characters kept of each string attribute value, a span, metric or unit name, a log body and the scope version.
MAX_STRING_CHARS = 1024

_WORKER = "worker"


@dto
@dataclass(frozen=True)
class Allowlist:
    """The one scope a span may arrive under and the attributes it may carry, each with its declared value
    type: ``string``, ``int`` or ``double``. ``None`` for either admits any scope, or any attribute, the
    caps still holding. ``stamp_runner`` also stamps the runner's id on each kept span."""

    scope: str | None
    attributes: Mapping[str, str] | None
    stamp_runner: bool = False


@dto
@dataclass(frozen=True)
class Admission[T]:
    """``kept`` are the rebuilt items to forward; ``dropped`` counts those refused."""

    kept: list[T]
    dropped: int


def lease_trace_id(lease: Lease) -> int:
    """The only trace a worker is handed: its chunk's."""
    return chunk_trace_id(lease.chunk_id)


def admit(spans: list[ReceivedSpan], lease: Lease, allowlist: Allowlist) -> Admission[ReceivedSpan]:
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


def _rebuilt(span: ReceivedSpan, lease: Lease, allowlist: Allowlist) -> ReceivedSpan:
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


def admit_data_points(points: list[ReceivedDataPoint], lease: Lease, scope: str) -> Admission[ReceivedDataPoint]:
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


def admit_log_records(records: list[ReceivedLogRecord], lease: Lease, scope: str) -> Admission[ReceivedLogRecord]:
    """The log records under ``scope``, each rebuilt as :func:`admit_data_points` does, with its body capped and
    its trace context kept only when it names the lease's work trace."""
    expected = lease_trace_id(lease)
    kept = [
        _log_rebuilt(record, lease, in_trace=record.trace_id == expected)
        for record in records
        if record.scope_name == scope
    ]
    return Admission(kept=kept, dropped=len(records) - len(kept))


def _log_rebuilt(record: ReceivedLogRecord, lease: Lease, *, in_trace: bool) -> ReceivedLogRecord:
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


def _stamped_attributes(sent: Mapping[str, Scalar], lease: Lease) -> dict[str, Scalar]:
    attributes = {key: _truncated(value) for key, value in list(sent.items())[:MAX_ATTRIBUTES]}
    _stamp(attributes, lease)
    attributes[RUNNER_ID] = lease.runner_id
    return attributes


def _stamp(attributes: dict[str, Scalar], lease: Lease) -> None:
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


#: ``service.name`` on every span a worker's CLI sends, whatever its own resource said.
CLI_SERVICE_NAME = "blizzard-cli"
#: ``service.name`` on every other worker program's spans, kept only under ``[tracing] worker_programs``.
PROGRAM_SERVICE_NAME = "blizzard-worker-program"

_CLI_ALLOWLIST = Allowlist(scope=CLI_SCOPE, attributes=CLI_ATTRIBUTES)
_PROGRAM_ALLOWLIST = Allowlist(scope=None, attributes=None)
_CLAUDE_CODE_ALLOWLIST = Allowlist(scope=CLAUDE_CODE_TRACING_SCOPE, attributes=None, stamp_runner=True)


@dto
@dataclass(frozen=True)
class SpanRouting:
    """One trace export, routed. ``claude`` is Claude Code's own tracing scope (kept only under
    ``harness_telemetry``); ``cli`` the worker CLI's kept spans, forwarded under :data:`CLI_SERVICE_NAME`;
    ``others`` the other programs' kept spans (only under ``worker_programs``). ``rest_received`` counts
    spans outside Claude Code's scope; ``accepted``/``dropped`` the non-Claude spans kept and refused."""

    claude: Admission[ReceivedSpan]
    claude_received: int
    cli: list[ReceivedSpan]
    others: list[ReceivedSpan]
    rest_received: int
    accepted: int
    dropped: int

    @property
    def kept(self) -> int:
        """Every span kept, both families — what the lease's span rate is charged."""
        return len(self.claude.kept) + self.accepted

    @property
    def refused(self) -> int:
        """Every span refused, both families — what the export response names."""
        return self.claude.dropped + self.dropped


def route_spans(spans: list[ReceivedSpan], lease: Lease, *, programs: bool, harness: bool) -> SpanRouting:
    """Route each span to the one allowlist it is admitted against, exactly once: Claude Code's
    tracing scope under ``harness``; the CLI's scope against its declared attributes; everything
    else against the wildcard under ``programs``, or the CLI allowlist (refusing it) otherwise —
    so a CLI span's declared attributes are never cut by the wildcard's cap first."""
    claude = [span for span in spans if harness and span.scope_name == CLAUDE_CODE_TRACING_SCOPE]
    rest = [span for span in spans if not (harness and span.scope_name == CLAUDE_CODE_TRACING_SCOPE)]
    claude_admission = admit(claude, lease, _CLAUDE_CODE_ALLOWLIST)
    cli_spans = [span for span in rest if programs and span.scope_name == CLI_SCOPE]
    program_spans = [span for span in rest if not (programs and span.scope_name == CLI_SCOPE)]
    admission = admit(program_spans, lease, _PROGRAM_ALLOWLIST if programs else _CLI_ALLOWLIST)
    cli_admission = admit(cli_spans, lease, _CLI_ALLOWLIST)
    cli = [*cli_admission.kept, *(span for span in admission.kept if not programs and span.scope_name == CLI_SCOPE)]
    return SpanRouting(
        claude=claude_admission,
        claude_received=len(claude),
        cli=cli,
        others=[span for span in admission.kept if programs],
        rest_received=len(rest),
        accepted=len(admission.kept) + len(cli_admission.kept),
        dropped=admission.dropped + cli_admission.dropped,
    )


def service_name_for(scope_name: str, mapped: Mapping[str, str]) -> str:
    """The ``service.name`` a non-CLI span is forwarded under: the operator's mapping for its
    scope, else Claude Code's for its own scopes, else the generic worker program's."""
    default = CLAUDE_CODE_SERVICE_NAME if scope_name in CLAUDE_CODE_SCOPES else PROGRAM_SERVICE_NAME
    return mapped.get(scope_name, default)


def spans_by_service_name(spans: list[ReceivedSpan], mapped: Mapping[str, str]) -> dict[str, list[ReceivedSpan]]:
    groups: dict[str, list[ReceivedSpan]] = {}
    for span in spans:
        groups.setdefault(service_name_for(span.scope_name, mapped), []).append(span)
    return groups
