"""The one platform-tracing handle a daemon process builds at its composition root.

Off unless both switches are on: ``[tracing] platform`` and an OTLP endpoint the fleet-trace settings
accept. The SDK pipeline and every instrumentation package are imported only on the enabled path."""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from typing import TYPE_CHECKING, Any, Protocol

from fastapi.telemetry import TelemetryConfig

from blizzard.foundation.platform_tracing.received import ReceivedSpan
from blizzard.foundation.platform_tracing.tracer import IPlatformTracer, NoopPlatformTracer
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.settings import TracingSettings

if TYPE_CHECKING:
    import httpx
    from opentelemetry.sdk.trace.export import SpanExporter
    from sqlalchemy import Engine

ScopeFilter = Callable[[MutableMapping[str, Any]], bool]
#: The attributes a span carries, asked as it starts; a span started while this answers ``None`` is never exported.
SpanStamp = Callable[[], Mapping[str, str] | None]


def platform_tracing_enabled(config: TracingConfig, environ: Mapping[str, str]) -> bool:
    return config.platform and TracingSettings.of(environ).enabled()


class IPlatformTracing(Protocol):
    @property
    def enabled(self) -> bool:
        """Whether platform spans are being recorded by this process."""
        ...

    @property
    def tracer(self) -> IPlatformTracer: ...

    def fastapi_telemetry(self, *, exclude: ScopeFilter | None = None) -> TelemetryConfig:
        """The ``FastAPI(telemetry=...)`` config for one app; ``exclude`` skips a request's server span."""
        ...

    def instrument_engine(self, engine: Engine) -> None: ...

    def instrument_client(self, client: httpx.Client | httpx.AsyncClient) -> None: ...

    def suppressed(self) -> AbstractContextManager[object]:
        """No span of any instrumentation starts inside — an excluded request's children included."""
        ...

    def forward(self, spans: Sequence[ReceivedSpan], resource_service_name: str) -> None:
        """Hand already-admitted spans to the process's own export pipeline, under this process's resource
        with ``service.name`` replaced by ``resource_service_name``. A no-op when platform tracing is off."""
        ...

    def shutdown(self, timeout: float) -> None:
        """Flush what is buffered, waiting at most ``timeout`` seconds, then stop."""
        ...


class DisabledPlatformTracing:
    def __init__(self) -> None:
        self._tracer = NoopPlatformTracer()

    @property
    def enabled(self) -> bool:
        return False

    @property
    def tracer(self) -> IPlatformTracer:
        return self._tracer

    def fastapi_telemetry(self, *, exclude: ScopeFilter | None = None) -> TelemetryConfig:
        return {"tracing": False, "auto_configure": False}

    def instrument_engine(self, engine: Engine) -> None:
        return None

    def instrument_client(self, client: httpx.Client | httpx.AsyncClient) -> None:
        return None

    def suppressed(self) -> AbstractContextManager[object]:
        return nullcontext()

    def forward(self, spans: Sequence[ReceivedSpan], resource_service_name: str) -> None:
        return None

    def shutdown(self, timeout: float) -> None:
        return None


def build_platform_tracing(
    config: TracingConfig,
    environ: Mapping[str, str],
    *,
    resource: Mapping[str, str],
    scope: str,
    scope_version: str,
    stamp: SpanStamp | None = None,
    exporter: SpanExporter | None = None,
) -> IPlatformTracing:
    """The process's handle. ``stamp`` is asked for each span's attributes as the span starts, and a
    span started while it answers ``None`` is never exported; ``exporter`` replaces the OTLP exporter —
    tests pass an in-memory one."""
    if not platform_tracing_enabled(config, environ):
        return DisabledPlatformTracing()
    from blizzard.foundation.platform_tracing.internal.pipeline import EnabledPlatformTracing

    return EnabledPlatformTracing.build(
        config.platform_sample_ratio,
        environ,
        resource=resource,
        scope=scope,
        scope_version=scope_version,
        stamp=stamp,
        exporter=exporter,
    )
