"""The enabled platform-tracing pipeline: one provider per process, never installed globally — each
instrumentation is handed it explicitly."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager

import httpx
from fastapi.telemetry import TelemetryConfig
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import __version__ as sqlalchemy_instrumentation_version
from opentelemetry.instrumentation.sqlalchemy.engine import EngineTracer
from opentelemetry.instrumentation.utils import suppress_instrumentation
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceFlags
from sqlalchemy import Engine

from blizzard.foundation.platform_tracing.handle import ScopeFilter
from blizzard.foundation.platform_tracing.internal.redaction import RedactingExporter
from blizzard.foundation.platform_tracing.internal.sampling import sampler
from blizzard.foundation.platform_tracing.internal.semconv import pin_stable_semconv
from blizzard.foundation.platform_tracing.received import ReceivedSpan
from blizzard.foundation.platform_tracing.semconv import DATABASE_SEMCONV_VERSION
from blizzard.foundation.platform_tracing.span_context import span_context
from blizzard.foundation.platform_tracing.tracer import IPlatformTracer
from blizzard.foundation.trace_ids import DerivedContext
from blizzard.foundation.trace_spans import Attributes

_SQLALCHEMY_SCOPE = "opentelemetry.instrumentation.sqlalchemy"


class _Stamping(SpanProcessor):
    def __init__(self, attributes: Mapping[str, str]) -> None:
        self._attributes = dict(attributes)

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        span.set_attributes(self._attributes)


class _SdkTracer:
    def __init__(self, tracer: trace.Tracer) -> None:
        self._tracer = tracer

    def root(self, name: str, attributes: Attributes | None = None) -> AbstractContextManager[None]:
        return self._span(name, attributes, Context())

    def child(self, name: str, attributes: Attributes | None = None) -> AbstractContextManager[None]:
        return self._span(name, attributes, None)

    @contextmanager
    def under(self, derived: DerivedContext) -> Iterator[None]:
        parent = trace.set_span_in_context(trace.NonRecordingSpan(span_context(derived, remote=True)), Context())
        token = otel_context.attach(parent)
        try:
            yield
        finally:
            otel_context.detach(token)

    def link(self, derived: DerivedContext) -> None:
        span = trace.get_current_span()
        if span.is_recording():
            span.add_link(span_context(derived))

    @contextmanager
    def _span(self, name: str, attributes: Attributes | None, context: Context | None) -> Iterator[None]:
        # An exception's message can carry a secret, so the span records that it failed and never why.
        with self._tracer.start_as_current_span(
            name,
            context=context,
            attributes=dict(attributes or {}),
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                yield
            except BaseException:
                span.set_status(Status(StatusCode.ERROR))
                raise


def _readable(span: ReceivedSpan, resource: Resource) -> ReadableSpan:
    """A received span as a finished, sampled span: the batch processor drops an unsampled one."""
    flags = TraceFlags(TraceFlags.SAMPLED)
    parent = (
        SpanContext(span.trace_id, span.parent_span_id, is_remote=True, trace_flags=flags)
        if span.parent_span_id is not None
        else None
    )
    # OTLP numbers span kinds from 1 (internal), the SDK's enum from 0.
    kind = SpanKind(span.kind - 1) if 1 <= span.kind <= len(SpanKind) else SpanKind.INTERNAL
    code = StatusCode(span.status_code) if span.status_code in (0, 1, 2) else StatusCode.UNSET
    return ReadableSpan(
        name=span.name,
        context=SpanContext(span.trace_id, span.span_id, is_remote=False, trace_flags=flags),
        parent=parent,
        resource=resource,
        attributes=dict(span.attributes),
        kind=kind,
        status=Status(code),
        start_time=span.start_time_ns,
        end_time=span.end_time_ns,
        instrumentation_scope=InstrumentationScope(span.scope_name, span.scope_version or None),
    )


class EnabledPlatformTracing:
    def __init__(self, provider: TracerProvider, tracer: IPlatformTracer, processor: BatchSpanProcessor) -> None:
        self._provider = provider
        self._tracer = tracer
        self._processor = processor
        self._engine_tracers: list[EngineTracer] = []

    @classmethod
    def build(
        cls,
        ratio: float,
        environ: Mapping[str, str],
        *,
        resource: Mapping[str, str],
        scope: str,
        scope_version: str,
        stamped: Mapping[str, str],
        exporter: SpanExporter | None,
    ) -> EnabledPlatformTracing:
        pin_stable_semconv()
        provider = TracerProvider(
            sampler=sampler(ratio, environ), resource=Resource.create(dict(resource)), shutdown_on_exit=False
        )
        if stamped:
            provider.add_span_processor(_Stamping(stamped))
        processor = BatchSpanProcessor(RedactingExporter(exporter or OTLPSpanExporter()))
        provider.add_span_processor(processor)
        return cls(provider, _SdkTracer(provider.get_tracer(scope, scope_version)), processor)

    @property
    def tracer(self) -> IPlatformTracer:
        return self._tracer

    @property
    def enabled(self) -> bool:
        return True

    def forward(self, spans: Sequence[ReceivedSpan], resource_service_name: str) -> None:
        resource = self._provider.resource.merge(Resource({"service.name": resource_service_name}))
        for span in spans:
            self._processor.on_end(_readable(span, resource))

    def fastapi_telemetry(self, *, exclude: ScopeFilter | None = None) -> TelemetryConfig:
        return {
            "tracer_provider": self._provider,
            "tracing": True,
            "operation_spans": False,
            "metrics": False,
            "logs": False,
            "auto_configure": False,
            "exclude": exclude,
        }

    def instrument_engine(self, engine: Engine) -> None:
        tracer = self._provider.get_tracer(
            _SQLALCHEMY_SCOPE,
            sqlalchemy_instrumentation_version,
            schema_url=f"https://opentelemetry.io/schemas/{DATABASE_SEMCONV_VERSION}",
        )
        usage = NoOpMeterProvider().get_meter(_SQLALCHEMY_SCOPE).create_up_down_counter("db.client.connections.usage")
        self._engine_tracers.append(EngineTracer(tracer, engine, usage, enable_commenter=False))

    def instrument_client(self, client: httpx.Client | httpx.AsyncClient) -> None:
        HTTPXClientInstrumentor.instrument_client(client, tracer_provider=self._provider)

    def suppressed(self) -> AbstractContextManager[object]:
        return suppress_instrumentation()

    def shutdown(self, timeout: float) -> None:
        self._provider.force_flush(timeout_millis=int(timeout * 1000))
        self._provider.shutdown()
