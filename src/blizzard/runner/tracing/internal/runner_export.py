"""Construct the runner's trace exporter from its declared inputs."""

from collections.abc import Iterator

from blizzard import __version__
from blizzard.foundation.trace_export import otlp_trace_exporter
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.runner.tracing.attributes import INSTRUMENTATION_SCOPE, INSTRUMENTATION_SCOPE_VERSION, resource_attributes
from blizzard.runner.tracing.bounded_export import BoundedTraceExporter, BoundedTraceExporters
from blizzard.runner.tracing.runner_export import RunnerTraceExport, RunnerTraceOptions


class RunnerTraceExportFactory:
    """Build per-runner transports, or share the guard on a borrowed exporter without owning it."""

    def __init__(self, shared: BoundedTraceExporters) -> None:
        self._shared = shared

    def build(self, options: RunnerTraceOptions) -> RunnerTraceExport:
        if not TracingSettings.of(options.environ).enabled():
            return RunnerTraceExport(None)
        if options.injected is not None:
            return RunnerTraceExport(self._shared.for_exporter(options.injected))
        exporter = otlp_trace_exporter(
            resource_attributes(options.environ, __version__),
            scope=INSTRUMENTATION_SCOPE,
            scope_version=INSTRUMENTATION_SCOPE_VERSION,
        )
        return RunnerTraceExport(BoundedTraceExporter(exporter), exporter.close)


def runner_trace_export(factory: RunnerTraceExportFactory, options: RunnerTraceOptions) -> Iterator[RunnerTraceExport]:
    trace = factory.build(options)
    try:
        yield trace
    finally:
        trace.close()
