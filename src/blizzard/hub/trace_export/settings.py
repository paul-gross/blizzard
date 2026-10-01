"""Fleet-trace enablement, read only from OpenTelemetry's own environment variables.

A pure parse: no exporter is built here, and endpoint, headers, timeout, compression and
certificates stay the SDK's own concern — this module reads only what decides whether
the hub can export at all (``blizzard-product:/plans/tracing/fleet-spans/spec/emission.md``
§Configuration)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

ENV_TRACES_ENDPOINT = "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
ENV_ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"
ENV_TRACES_EXPORTER = "OTEL_TRACES_EXPORTER"
ENV_SDK_DISABLED = "OTEL_SDK_DISABLED"
ENV_TRACES_PROTOCOL = "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL"
ENV_PROTOCOL = "OTEL_EXPORTER_OTLP_PROTOCOL"

#: The one OTLP transport the hub exports over.
SUPPORTED_PROTOCOL = "http/protobuf"

TracingState = Literal["enabled", "disabled", "rejected"]


@dataclass(frozen=True)
class TracingSettings:
    """Whether fleet tracing runs: ``enabled``, ``disabled``, or ``rejected`` — configured
    in a way the hub cannot honor, naming the offending ``setting`` and its ``value``."""

    state: TracingState
    setting: str | None = None
    value: str | None = None

    @classmethod
    def of(cls, environ: Mapping[str, str]) -> TracingSettings:
        def read(name: str) -> str:
            return environ.get(name, "").strip()

        if not (read(ENV_TRACES_ENDPOINT) or read(ENV_ENDPOINT)):
            return cls("disabled")
        if read(ENV_TRACES_EXPORTER).lower() == "none":
            return cls("disabled")
        if read(ENV_SDK_DISABLED).lower() == "true":
            return cls("disabled")
        # The traces-specific variable overrides the general one, as the SDK resolves it.
        for name in (ENV_TRACES_PROTOCOL, ENV_PROTOCOL):
            protocol = read(name)
            if protocol:
                if protocol != SUPPORTED_PROTOCOL:
                    return cls("rejected", setting=name, value=protocol)
                break
        return cls("enabled")

    def enabled(self) -> bool:
        return self.state == "enabled"

    @property
    def rejection_message(self) -> str:
        """The operator-facing line a ``trace-config-rejected`` event carries."""
        return (
            f"fleet tracing is off: {self.setting}={self.value!r} is not supported; "
            f"the hub exports only {SUPPORTED_PROTOCOL}"
        )
