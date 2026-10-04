"""Where an OTLP/JSON span is posted, read only from OpenTelemetry's own environment variables —
stdlib only, so a short-lived command resolves it without the SDK.

Only the destination is read: the CLI always sends JSON, so ``OTEL_EXPORTER_OTLP_PROTOCOL`` is not
consulted (``blizzard-product:/delivered/tracing/platform-spans/spec/instrumentation.md`` §Configuration)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import unquote

from blizzard.foundation.roles import dto
from blizzard.foundation.trace_export.settings import (
    ENV_TRACES_ENDPOINT,
    configured_endpoint,
    export_switched_off,
)

ENV_TRACES_HEADERS = "OTEL_EXPORTER_OTLP_TRACES_HEADERS"
ENV_HEADERS = "OTEL_EXPORTER_OTLP_HEADERS"

TRACES_PATH = "/v1/traces"


@dto
@dataclass(frozen=True)
class OtlpDestination:
    """The full URL to post to and the headers to send with it."""

    url: str
    headers: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, environ: Mapping[str, str]) -> OtlpDestination | None:
        """The configured destination, or ``None`` when no endpoint is set or export is switched off.

        A signal-specific endpoint is used verbatim; the general one gets ``/v1/traces`` appended."""
        endpoint = configured_endpoint(environ)
        if not endpoint or export_switched_off(environ):
            return None
        if not environ.get(ENV_TRACES_ENDPOINT, "").strip():
            endpoint = f"{endpoint.rstrip('/')}{TRACES_PATH}"
        raw_headers = environ.get(ENV_TRACES_HEADERS, "").strip() or environ.get(ENV_HEADERS, "")
        return cls(endpoint, parse_headers(raw_headers))


def parse_headers(raw: str) -> dict[str, str]:
    """``key=value`` pairs, comma-separated, values percent-decoded; a pair without a key or ``=`` is skipped."""
    headers: dict[str, str] = {}
    for pair in raw.split(","):
        key, sep, value = pair.partition("=")
        if sep and key.strip():
            headers[key.strip()] = unquote(value.strip())
    return headers
