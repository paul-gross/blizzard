"""The enabled platform-tracing pipeline: one provider per process, never installed globally — each
instrumentation is handed it explicitly."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager

import httpx
from fastapi.telemetry import TelemetryConfig
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import __version__ as sqlalchemy_instrumentation_version
from opentelemetry.instrumentation.sqlalchemy.engine import EngineTracer
from opentelemetry.instrumentation.utils import suppress_instrumentation
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from sqlalchemy import Engine

from blizzard.foundation.platform_tracing.handle import ScopeFilter
from blizzard.foundation.platform_tracing.internal.redaction import RedactingExporter
from blizzard.foundation.platform_tracing.internal.sampling import sampler
from blizzard.foundation.platform_tracing.internal.semconv import pin_stable_semconv
from blizzard.foundation.platform_tracing.semconv import DATABASE_SEMCONV_VERSION
from blizzard.foundation.platform_tracing.tracer import IPlatformTracer
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
    def _span(self, name: str, attributes: Attributes | None, context: Context | None) -> Iterator[None]:
        with self._tracer.start_as_current_span(
            name, context=context, attributes=dict(attributes or {}), record_exception=False
        ):
            yield


class EnabledPlatformTracing:
    def __init__(self, provider: TracerProvider, tracer: IPlatformTracer) -> None:
        self._provider = provider
        self._tracer = tracer
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
        provider.add_span_processor(BatchSpanProcessor(RedactingExporter(exporter or OTLPSpanExporter())))
        return cls(provider, _SdkTracer(provider.get_tracer(scope, scope_version)))

    @property
    def tracer(self) -> IPlatformTracer:
        return self._tracer

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
