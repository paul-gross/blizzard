from __future__ import annotations

from opentelemetry.instrumentation._semconv import (
    _OpenTelemetrySemanticConventionStability,
    _OpenTelemetryStabilitySignalType,
    _StabilityMode,
)

_PINNED = {
    _OpenTelemetryStabilitySignalType.HTTP: _StabilityMode.HTTP,
    _OpenTelemetryStabilitySignalType.DATABASE: _StabilityMode.DATABASE,
    _OpenTelemetryStabilitySignalType.GEN_AI: _StabilityMode.DEFAULT,
}


def pin_stable_semconv() -> None:
    """Force the stable HTTP and database shapes for the whole process. The instrumentations read
    their mode once, so this runs before any of them initializes."""
    stability = _OpenTelemetrySemanticConventionStability
    with stability._lock:
        stability._OTEL_SEMCONV_STABILITY_SIGNAL_MAPPING = dict(_PINNED)
        stability._initialized = True
