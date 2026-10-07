"""The public seam onto the received-telemetry pipeline: the runner builds the enabled export through it, so the
OpenTelemetry-importing pipeline stays private to ``platform_tracing``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.sdk._logs.export import LogRecordExporter
    from opentelemetry.sdk.metrics.export import MetricExporter

    from blizzard.foundation.platform_tracing.internal.received_pipeline import EnabledReceivedTelemetryExport


def build_enabled_received_export(
    *,
    resource: Mapping[str, str],
    metric_exporter: MetricExporter | None,
    log_exporter: LogRecordExporter | None,
    metrics: bool,
    logs: bool,
) -> EnabledReceivedTelemetryExport:
    from blizzard.foundation.platform_tracing.internal.received_pipeline import EnabledReceivedTelemetryExport

    return EnabledReceivedTelemetryExport.build(
        resource=resource, metric_exporter=metric_exporter, log_exporter=log_exporter, metrics=metrics, logs=logs
    )
