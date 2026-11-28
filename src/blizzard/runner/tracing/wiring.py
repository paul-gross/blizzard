"""The host telemetry area's registrations."""

from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.runner.tracing.bounded_export import BoundedTraceExporters
from blizzard.runner.tracing.internal.host_infrastructure import (
    HarnessSpanCounter as HarnessSpanCounter,
)
from blizzard.runner.tracing.internal.host_infrastructure import (
    LogBounds as LogBounds,
)
from blizzard.runner.tracing.internal.host_infrastructure import (
    MetricBounds as MetricBounds,
)
from blizzard.runner.tracing.internal.host_infrastructure import (
    SpanCounter as SpanCounter,
)
from blizzard.runner.tracing.internal.host_infrastructure import (
    harness_span_counter,
    log_bounds,
    metric_bounds,
    platform_tracing,
    received_export,
    receiver_tracing,
    span_counter,
    span_limiter,
    telemetry_receiver,
)
from blizzard.runner.tracing.internal.runner_export import RunnerTraceExportFactory, runner_trace_export
from blizzard.runner.tracing.runner_export import RunnerTraceOptions


def register_host_infrastructure(registry: Registry) -> None:
    registry.service(BoundedTraceExporters, lifetime=Lifetime.PROCESS)
    registry.service(RunnerTraceExportFactory, lifetime=Lifetime.PROCESS)
    registry.context(port(RunnerTraceOptions), lifetime=Lifetime.RUNNER)
    registry.resource(runner_trace_export, lifetime=Lifetime.RUNNER)
    registry.resource(platform_tracing, lifetime=Lifetime.PROCESS)
    registry.resource(received_export, lifetime=Lifetime.PROCESS)
    registry.factory(span_limiter, lifetime=Lifetime.PROCESS)
    registry.factory(span_counter, lifetime=Lifetime.PROCESS)
    registry.factory(harness_span_counter, lifetime=Lifetime.PROCESS)
    registry.factory(metric_bounds, lifetime=Lifetime.PROCESS)
    registry.factory(log_bounds, lifetime=Lifetime.PROCESS)
    registry.factory(receiver_tracing, lifetime=Lifetime.PROCESS)
    registry.factory(telemetry_receiver, lifetime=Lifetime.PROCESS)
