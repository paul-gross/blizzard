"""The enabled platform-tracing pipeline: one provider per process, never installed globally — each
instrumentation is handed it explicitly."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager

import httpx2
from fastapi.telemetry import TelemetryConfig
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPX2ClientInstrumentor
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

from blizzard.foundation.platform_tracing.handle import ScopeFilter, SpanStamp
from blizzard.foundation.platform_tracing.internal.sampling import sampler
from blizzard.foundation.platform_tracing.internal.semconv import pin_stable_semconv
from blizzard.foundation.platform_tracing.received import ReceivedSpan
from blizzard.foundation.platform_tracing.redaction import RedactingExporter
from blizzard.foundation.platform_tracing.semconv import DATABASE_SEMCONV_VERSION
from blizzard.foundation.platform_tracing.span_context import span_context
from blizzard.foundation.platform_tracing.tracer import IPlatformTracer
from blizzard.foundation.trace_ids import DerivedContext
from blizzard.foundation.trace_spans import Attributes

_SQLALCHEMY_SCOPE = "opentelemetry.instrumentation.sqlalchemy"

#: The stamp the opener of a span tree chose, carried in the context every span under it opens in.
_STAMP_KEY = otel_context.create_key("blizzard.platform.stamp")


class _Carried:
    """The stamp a context carries, boxed so the key's value is recognisably one."""

    def __init__(self, stamp: SpanStamp) -> None:
        self.stamp = stamp


def _carried_stamp(context: Context | None) -> SpanStamp | None:
    carried = otel_context.get_value(_STAMP_KEY, context)
    return carried.stamp if isinstance(carried, _Carried) else None


@contextmanager
def _stamping(stamp: SpanStamp) -> Iterator[None]:
    token = otel_context.attach(otel_context.set_value(_STAMP_KEY, _Carried(stamp)))
    try:
        yield
    finally:
        otel_context.detach(token)


class _Stamping(SpanProcessor):
    """Stamps each span with what its stamp answers as it starts, then hands it on to ``export``; a span
    started while its stamp answers ``None`` never reaches ``export``, so it is never exported. A span's stamp is
    the one its parent context carries — so a span inherits its parent's — else the process's own ``stamp``."""

    def __init__(self, stamp: SpanStamp, export: SpanProcessor) -> None:
        self._stamp = stamp
        self._export = export
        self._withheld: set[int] = set()

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        attributes = (_carried_stamp(parent_context) or self._stamp)()
        if attributes is None:
            self._withheld.add(span.get_span_context().span_id)
            return
        span.set_attributes(attributes)
        self._export.on_start(span, parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        context = span.context
        if context is not None and context.span_id in self._withheld:
            self._withheld.discard(context.span_id)
            return
        self._export.on_end(span)

    def shutdown(self) -> None:
        self._export.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._export.force_flush(timeout_millis)


def _fresh() -> Context:
    """An empty context — a new trace's, or a remote parent's — still carrying the stamp its opener runs under."""
    carried = _carried_stamp(None)
    return Context() if carried is None else otel_context.set_value(_STAMP_KEY, _Carried(carried), Context())


class _SdkTracer:
    def __init__(self, tracer: trace.Tracer) -> None:
        self._tracer = tracer

    def root(self, name: str, attributes: Attributes | None = None) -> AbstractContextManager[None]:
        return self._span(name, attributes, _fresh())

    def child(self, name: str, attributes: Attributes | None = None) -> AbstractContextManager[None]:
        return self._span(name, attributes, None)

    @contextmanager
    def under(self, derived: DerivedContext) -> Iterator[None]:
        parent = trace.set_span_in_context(trace.NonRecordingSpan(span_context(derived, remote=True)), _fresh())
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


class _StampedTracer:
    """A tracer whose every span opens under ``stamp``, so it and its descendants carry what ``stamp`` answers."""

    def __init__(self, tracer: IPlatformTracer, stamp: SpanStamp) -> None:
        self._tracer = tracer
        self._stamp = stamp

    @contextmanager
    def root(self, name: str, attributes: Attributes | None = None) -> Iterator[None]:
        with _stamping(self._stamp), self._tracer.root(name, attributes):
            yield

    @contextmanager
    def child(self, name: str, attributes: Attributes | None = None) -> Iterator[None]:
        with _stamping(self._stamp), self._tracer.child(name, attributes):
            yield

    @contextmanager
    def under(self, derived: DerivedContext) -> Iterator[None]:
        with self._tracer.under(derived), _stamping(self._stamp):
            yield

    def link(self, derived: DerivedContext) -> None:
        self._tracer.link(derived)


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
        stamp: SpanStamp | None,
        exporter: SpanExporter | None,
    ) -> EnabledPlatformTracing:
        pin_stable_semconv()
        provider = TracerProvider(
            sampler=sampler(ratio, environ), resource=Resource.create(dict(resource)), shutdown_on_exit=False
        )
        processor = BatchSpanProcessor(RedactingExporter(exporter or OTLPSpanExporter()))
        provider.add_span_processor(processor if stamp is None else _Stamping(stamp, processor))
        return cls(provider, _SdkTracer(provider.get_tracer(scope, scope_version)), processor)

    @property
    def tracer(self) -> IPlatformTracer:
        return self._tracer

    def stamping(self, stamp: SpanStamp) -> AbstractContextManager[None]:
        return _stamping(stamp)

    def stamped(self, stamp: SpanStamp) -> IPlatformTracer:
        return _StampedTracer(self._tracer, stamp)

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

    def instrument_client(self, client: httpx2.Client | httpx2.AsyncClient) -> None:
        HTTPX2ClientInstrumentor.instrument_client(client, tracer_provider=self._provider)

    def suppressed(self) -> AbstractContextManager[object]:
        return suppress_instrumentation()

    def shutdown(self, timeout: float) -> None:
        self._provider.force_flush(timeout_millis=int(timeout * 1000))
        self._provider.shutdown()
