"""Borrowed host tracing options."""

from collections.abc import Mapping
from dataclasses import dataclass

from blizzard.foundation.platform_tracing.handle import IPlatformTracing
from blizzard.foundation.roles import adapter_model
from blizzard.runner.harness.harness_telemetry_plan import HarnessTelemetryNames
from blizzard.runner.tracing.config import RunnerTracingConfig
from blizzard.runner.tracing.received_export import IReceivedTelemetryExport


@adapter_model
@dataclass(frozen=True)
class HostTracingOptions:
    config: RunnerTracingConfig
    environ: Mapping[str, str]
    telemetry_names: tuple[HarnessTelemetryNames, ...]
    platform_tracing: IPlatformTracing | None = None
    received_telemetry: IReceivedTelemetryExport | None = None
    trace_platform: bool = False
