from __future__ import annotations

# The pinned instrumentation exposes no public API to force process-wide modes.
from opentelemetry.instrumentation._semconv import (
    _OpenTelemetrySemanticConventionStability,  # pyright: ignore[reportPrivateUsage]
    _OpenTelemetryStabilitySignalType,  # pyright: ignore[reportPrivateUsage]
    _StabilityMode,  # pyright: ignore[reportPrivateUsage]
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
    # Atomically override the instrumentations' cached modes before initialization.
    with stability._lock:  # pyright: ignore[reportPrivateUsage]
        stability._OTEL_SEMCONV_STABILITY_SIGNAL_MAPPING = dict(_PINNED)  # pyright: ignore[reportPrivateUsage]
        stability._initialized = True  # pyright: ignore[reportPrivateUsage]
