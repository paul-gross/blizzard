"""The three OpenTelemetry signals, and whether this process's own OpenTelemetry variables let it export one.

The per-signal counterpart of :class:`~blizzard.foundation.trace_export.settings.TracingSettings`, which reads
the traces signal alone: the same switched-off and ``http/protobuf``-only rules, each signal's own variables
overriding the general ones as the SDK resolves them."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from blizzard.foundation.trace_export.settings import ENV_ENDPOINT, ENV_PROTOCOL, ENV_SDK_DISABLED, SUPPORTED_PROTOCOL


class TelemetrySignal(StrEnum):
    TRACES = "traces"
    METRICS = "metrics"
    LOGS = "logs"

    @property
    def endpoint_variable(self) -> str:
        return f"OTEL_EXPORTER_OTLP_{self.name}_ENDPOINT"

    @property
    def protocol_variable(self) -> str:
        return f"OTEL_EXPORTER_OTLP_{self.name}_PROTOCOL"

    @property
    def headers_variable(self) -> str:
        return f"OTEL_EXPORTER_OTLP_{self.name}_HEADERS"

    @property
    def exporter_variable(self) -> str:
        return f"OTEL_{self.name}_EXPORTER"


def signal_exportable(signal: TelemetrySignal, environ: Mapping[str, str]) -> bool:
    """Whether ``environ`` gives ``signal`` an OTLP/HTTP-protobuf destination and does not switch it off."""

    def read(name: str) -> str:
        return environ.get(name, "").strip()

    if not (read(signal.endpoint_variable) or read(ENV_ENDPOINT)):
        return False
    if read(signal.exporter_variable).lower() == "none" or read(ENV_SDK_DISABLED).lower() == "true":
        return False
    protocol = read(signal.protocol_variable) or read(ENV_PROTOCOL)
    return not protocol or protocol == SUPPORTED_PROTOCOL
