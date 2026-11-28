"""Host telemetry infrastructure and its owned export handles."""

from collections.abc import Iterator
from dataclasses import dataclass

from blizzard import __version__
from blizzard.foundation.clock import IClock, IMonotonicClock
from blizzard.foundation.platform_tracing.handle import (
    DisabledPlatformTracing,
    IPlatformTracing,
    build_platform_tracing,
)
from blizzard.foundation.roles import adapter_model
from blizzard.runner.tracing.attributes import resource_attributes
from blizzard.runner.tracing.host_options import HostTracingOptions
from blizzard.runner.tracing.platform import (
    PLATFORM_INSTRUMENTATION_SCOPE,
    PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
    host_stamp,
)
from blizzard.runner.tracing.received_export import (
    DisabledReceivedTelemetryExport,
    IReceivedTelemetryExport,
    build_received_telemetry_export,
)
from blizzard.runner.tracing.receiver_limits import ReceiverBounds, ReceiverCounter, SpanRateLimiter
from blizzard.runner.tracing.receiving import ReceiverTracing, ReceiverTracingInForce, TelemetryReceiver


@adapter_model
@dataclass(frozen=True)
class SpanCounter:
    value: ReceiverCounter


@adapter_model
@dataclass(frozen=True)
class HarnessSpanCounter:
    value: ReceiverCounter


@dataclass(frozen=True)
class MetricBounds:
    value: ReceiverBounds


@dataclass(frozen=True)
class LogBounds:
    value: ReceiverBounds


def platform_tracing(inputs: HostTracingOptions) -> Iterator[IPlatformTracing]:
    if not inputs.trace_platform:
        yield inputs.platform_tracing or DisabledPlatformTracing()
        return
    handle = build_platform_tracing(
        inputs.config,
        inputs.environ,
        resource=resource_attributes(inputs.environ, __version__),
        scope=PLATFORM_INSTRUMENTATION_SCOPE,
        scope_version=PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
        stamp=host_stamp(),
    )
    try:
        yield handle
    finally:
        handle.shutdown(3.0)


def received_export(inputs: HostTracingOptions, clock: IMonotonicClock) -> Iterator[IReceivedTelemetryExport]:
    if not inputs.trace_platform:
        yield inputs.received_telemetry or DisabledReceivedTelemetryExport()
        return
    handle = build_received_telemetry_export(
        inputs.config, inputs.environ, resource=resource_attributes(inputs.environ, __version__), monotonic_clock=clock
    )
    try:
        yield handle
    finally:
        handle.shutdown(3.0)


def span_limiter(clock: IClock) -> SpanRateLimiter:
    return SpanRateLimiter(clock)


def span_counter() -> SpanCounter:
    return SpanCounter(ReceiverCounter())


def harness_span_counter() -> HarnessSpanCounter:
    return HarnessSpanCounter(ReceiverCounter())


def metric_bounds(clock: IClock) -> MetricBounds:
    return MetricBounds(ReceiverBounds.fresh(clock))


def log_bounds(clock: IClock) -> LogBounds:
    return LogBounds(ReceiverBounds.fresh(clock))


def receiver_tracing(inputs: HostTracingOptions) -> ReceiverTracingInForce:
    def tracing() -> ReceiverTracing:
        return ReceiverTracing(
            worker_programs=inputs.config.worker_programs,
            harness_telemetry=inputs.config.harness_telemetry,
            mapped_services=inputs.config.worker_program_services,
        )

    return ReceiverTracingInForce(tracing)


def telemetry_receiver(
    inputs: HostTracingOptions,
    platform: IPlatformTracing,
    export: IReceivedTelemetryExport,
    limiter: SpanRateLimiter,
    spans: SpanCounter,
    harness: HarnessSpanCounter,
    metrics: MetricBounds,
    logs: LogBounds,
    clock: IClock,
    tracing: ReceiverTracingInForce,
) -> TelemetryReceiver:
    return TelemetryReceiver(
        platform,
        export,
        limiter,
        spans.value,
        harness.value,
        inputs.telemetry_names,
        metrics.value,
        logs.value,
        clock,
        tracing.current,
    )
