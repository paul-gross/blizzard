"""The OTLP ``http/protobuf`` binding of :class:`ITraceExporter` — the only OpenTelemetry import.

Span records become finished SDK span data carrying their derived contexts; no tracer, no global
provider, no instrumentation. Endpoint, headers, timeout, compression and certificates are left to
the SDK, which reads them from its own environment variables."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import Link, SpanContext, SpanKind, Status, StatusCode

from blizzard.foundation.platform_tracing.span_context import span_context
from blizzard.foundation.trace_spans import Attributes, SpanRecord
from blizzard.foundation.trace_spans import SpanKind as RecordKind
from blizzard.foundation.trace_spans import SpanStatus as RecordStatus

_KINDS = {RecordKind.INTERNAL: SpanKind.INTERNAL}
_STATUSES = {RecordStatus.UNSET: StatusCode.UNSET, RecordStatus.ERROR: StatusCode.ERROR}


def _nanos(at: datetime) -> int:
    return int(at.timestamp()) * 1_000_000_000 + at.microsecond * 1_000


def _attributes(attributes: Attributes) -> dict[str, str | int | float | bool | tuple[str, ...]]:
    return dict(attributes)


class OtlpTraceExporter:
    """Maps each :class:`SpanRecord` to a finished ``ReadableSpan`` and hands the batch to ``OTLPSpanExporter``."""

    def __init__(self, *, resource: Mapping[str, str], scope: str, scope_version: str) -> None:
        self._resource = Resource.create(dict(resource))
        self._named: dict[str, Resource] = {}
        self._scope = InstrumentationScope(scope, scope_version)
        self._exporter = OTLPSpanExporter()

    def export(self, spans: Sequence[SpanRecord]) -> bool:
        return self._exporter.export([self._readable(span) for span in spans]) is SpanExportResult.SUCCESS

    def _resource_of(self, span: SpanRecord) -> Resource:
        if span.service_name is None:
            return self._resource
        if span.service_name not in self._named:
            self._named[span.service_name] = Resource.create(
                {**self._resource.attributes, SERVICE_NAME: span.service_name}
            )
        return self._named[span.service_name]

    def _readable(self, span: SpanRecord) -> ReadableSpan:
        context = span_context(span.context)
        parent = (
            None
            if span.parent_span_id is None
            else SpanContext(context.trace_id, span.parent_span_id, is_remote=False, trace_flags=context.trace_flags)
        )
        return ReadableSpan(
            name=span.name,
            context=context,
            parent=parent,
            resource=self._resource_of(span),
            attributes=_attributes(span.attributes),
            events=[Event(e.name, _attributes(e.attributes), _nanos(e.time)) for e in span.events],
            links=[Link(span_context(link.context), _attributes(link.attributes)) for link in span.links],
            kind=_KINDS[span.kind],
            status=Status(_STATUSES[span.status]),
            start_time=_nanos(span.start),
            end_time=_nanos(span.end),
            instrumentation_scope=self._scope,
        )
