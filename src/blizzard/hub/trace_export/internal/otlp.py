"""The OTLP ``http/protobuf`` binding of :class:`ITraceExporter` — the hub's only OpenTelemetry import.

Span records become finished SDK span data carrying their derived contexts; no tracer, no global
provider, no instrumentation. Endpoint, headers, timeout, compression and certificates are left to
the SDK, which reads them from its own environment variables."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import Link, SpanContext, SpanKind, Status, StatusCode, TraceFlags

from blizzard.foundation.trace_ids import DerivedContext
from blizzard.foundation.trace_spans import Attributes, SpanRecord
from blizzard.foundation.trace_spans import SpanKind as RecordKind
from blizzard.foundation.trace_spans import SpanStatus as RecordStatus
from blizzard.hub.domain.tracing.attributes import INSTRUMENTATION_SCOPE, INSTRUMENTATION_SCOPE_VERSION

_KINDS = {RecordKind.INTERNAL: SpanKind.INTERNAL}
_STATUSES = {RecordStatus.UNSET: StatusCode.UNSET, RecordStatus.ERROR: StatusCode.ERROR}


def _nanos(at: datetime) -> int:
    return int(at.timestamp()) * 1_000_000_000 + at.microsecond * 1_000


def _context(derived: DerivedContext) -> SpanContext:
    return SpanContext(derived.trace_id, derived.span_id, is_remote=False, trace_flags=TraceFlags(derived.trace_flags))


def _attributes(attributes: Attributes) -> dict[str, str | int | float | bool | tuple[str, ...]]:
    return dict(attributes)


class OtlpTraceExporter:
    """Maps each :class:`SpanRecord` to a finished ``ReadableSpan`` and hands the batch to ``OTLPSpanExporter``."""

    def __init__(self, *, resource: Mapping[str, str]) -> None:
        self._resource = Resource.create(dict(resource))
        self._scope = InstrumentationScope(INSTRUMENTATION_SCOPE, INSTRUMENTATION_SCOPE_VERSION)
        self._exporter = OTLPSpanExporter()

    def export(self, spans: Sequence[SpanRecord]) -> bool:
        return self._exporter.export([self._readable(span) for span in spans]) is SpanExportResult.SUCCESS

    def _readable(self, span: SpanRecord) -> ReadableSpan:
        context = _context(span.context)
        parent = (
            None
            if span.parent_span_id is None
            else SpanContext(context.trace_id, span.parent_span_id, is_remote=False, trace_flags=context.trace_flags)
        )
        return ReadableSpan(
            name=span.name,
            context=context,
            parent=parent,
            resource=self._resource,
            attributes=_attributes(span.attributes),
            events=[Event(e.name, _attributes(e.attributes), _nanos(e.time)) for e in span.events],
            links=[Link(_context(link.context), _attributes(link.attributes)) for link in span.links],
            kind=_KINDS[span.kind],
            status=Status(_STATUSES[span.status]),
            start_time=_nanos(span.start),
            end_time=_nanos(span.end),
            instrumentation_scope=self._scope,
        )
