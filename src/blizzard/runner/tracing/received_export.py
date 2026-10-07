"""The received-telemetry export handle: where admitted metric data points and log records leave the process.

A separate narrow seam beside :mod:`~blizzard.foundation.platform_tracing.handle`: the hub shares that handle
and never forwards metrics or logs. Each signal exports only when this process's own OpenTelemetry variables give
it a destination, and the whole handle is off unless ``[tracing] platform`` and ``harness_telemetry`` are both on."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from blizzard.foundation.platform_tracing.handle import platform_tracing_enabled
from blizzard.foundation.platform_tracing.received import ReceivedDataPoint, ReceivedLogRecord
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.runner.harness.telemetry_signals import TelemetrySignal, signal_exportable


class IReceivedTelemetryExport(Protocol):
    def forward_metrics(self, points: Sequence[ReceivedDataPoint], resource_service_name: str) -> None:
        """Hand already-admitted data points to this process's metrics export, under its resource with
        ``service.name`` replaced by ``resource_service_name``. A no-op where metrics have no destination."""
        ...

    def forward_logs(self, records: Sequence[ReceivedLogRecord], resource_service_name: str) -> None:
        """As :meth:`forward_metrics`, for admitted log records."""
        ...

    def shutdown(self, timeout: float) -> None:
        """Flush what is buffered, waiting at most ``timeout`` seconds, then stop."""
        ...


class DisabledReceivedTelemetryExport:
    def forward_metrics(self, points: Sequence[ReceivedDataPoint], resource_service_name: str) -> None:
        return None

    def forward_logs(self, records: Sequence[ReceivedLogRecord], resource_service_name: str) -> None:
        return None

    def shutdown(self, timeout: float) -> None:
        return None


def build_received_telemetry_export(
    config: TracingConfig,
    environ: Mapping[str, str],
    *,
    resource: Mapping[str, str],
    metric_exporter: Any | None = None,
    log_exporter: Any | None = None,
) -> IReceivedTelemetryExport:
    """The process's handle. ``metric_exporter`` and ``log_exporter`` replace the OTLP exporters, and each
    counts as that signal's destination — tests pass in-memory ones."""
    if not (config.harness_telemetry and platform_tracing_enabled(config, environ)):
        return DisabledReceivedTelemetryExport()
    from blizzard.foundation.platform_tracing.received_pipeline import build_enabled_received_export

    return build_enabled_received_export(
        resource=resource,
        metric_exporter=metric_exporter,
        log_exporter=log_exporter,
        metrics=metric_exporter is not None or signal_exportable(TelemetrySignal.METRICS, environ),
        logs=log_exporter is not None or signal_exportable(TelemetrySignal.LOGS, environ),
    )
