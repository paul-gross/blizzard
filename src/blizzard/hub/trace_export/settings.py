"""Fleet-trace enablement, read only from OpenTelemetry's own environment variables.

A pure parse: no exporter is built here, and endpoint, headers, timeout, compression and
certificates stay the SDK's own concern — this module reads only what decides whether
the hub can export at all (``blizzard-product:/plans/tracing/fleet-spans/spec/emission.md``
§Configuration)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

ENV_TRACES_ENDPOINT = "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
ENV_ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"
ENV_TRACES_EXPORTER = "OTEL_TRACES_EXPORTER"
ENV_SDK_DISABLED = "OTEL_SDK_DISABLED"
ENV_TRACES_PROTOCOL = "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL"
ENV_PROTOCOL = "OTEL_EXPORTER_OTLP_PROTOCOL"

#: The one OTLP transport the hub exports over.
SUPPORTED_PROTOCOL = "http/protobuf"

#: What an endpoint that does not parse as a URL reads as — the raw value is never echoed.
UNPARSEABLE_ENDPOINT = "<unparseable endpoint>"

TracingState = Literal["enabled", "disabled", "rejected"]


def endpoint_origin(raw: str) -> str:
    """The scheme, host and port of an endpoint — never its userinfo, path, query or fragment,
    which can carry a credential. A value that is not an absolute URL reads as
    :data:`UNPARSEABLE_ENDPOINT`."""
    try:
        parts = urlsplit(raw)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return UNPARSEABLE_ENDPOINT
    if not parts.scheme or not host:
        return UNPARSEABLE_ENDPOINT
    shown = f"[{host}]" if ":" in host else host
    return f"{parts.scheme.lower()}://{shown}" + (f":{port}" if port is not None else "")


@dataclass(frozen=True)
class TracingSettings:
    """Whether fleet tracing runs: ``enabled``, ``disabled``, or ``rejected`` — configured
    in a way the hub cannot honor, naming the offending ``setting`` and its ``value``."""

    state: TracingState
    setting: str | None = None
    value: str | None = None
    #: The configured endpoint's origin, redacted once here so no later layer holds the raw value.
    endpoint: str | None = None

    @classmethod
    def of(cls, environ: Mapping[str, str]) -> TracingSettings:
        def read(name: str) -> str:
            return environ.get(name, "").strip()

        # The traces-specific variable overrides the general one, as the SDK resolves it.
        raw_endpoint = read(ENV_TRACES_ENDPOINT) or read(ENV_ENDPOINT)
        if not raw_endpoint:
            return cls("disabled")
        endpoint = endpoint_origin(raw_endpoint)
        if read(ENV_TRACES_EXPORTER).lower() == "none":
            return cls("disabled")
        if read(ENV_SDK_DISABLED).lower() == "true":
            return cls("disabled")
        for name in (ENV_TRACES_PROTOCOL, ENV_PROTOCOL):
            protocol = read(name)
            if protocol:
                if protocol != SUPPORTED_PROTOCOL:
                    return cls("rejected", setting=name, value=protocol, endpoint=endpoint)
                break
        return cls("enabled", endpoint=endpoint)

    def enabled(self) -> bool:
        return self.state == "enabled"

    @property
    def rejection_message(self) -> str:
        """The operator-facing line a ``trace-config-rejected`` event carries."""
        return (
            f"fleet tracing is off: {self.setting}={self.value!r} is not supported; "
            f"the hub exports only {SUPPORTED_PROTOCOL}"
        )
