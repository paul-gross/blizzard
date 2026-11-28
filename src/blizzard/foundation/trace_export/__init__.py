"""Trace-export adapter construction."""

from collections.abc import Mapping

from blizzard.foundation.trace_export.exporter import IOwnedTraceExporter


def otlp_trace_exporter(resource: Mapping[str, str], *, scope: str, scope_version: str) -> IOwnedTraceExporter:
    """Construct an owned OTLP transport, loading the SDK only when a transport is needed."""
    from blizzard.foundation.trace_export.internal.otlp import OtlpTraceExporter

    return OtlpTraceExporter(resource=resource, scope=scope, scope_version=scope_version)
