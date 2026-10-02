from __future__ import annotations

from collections.abc import Mapping

from opentelemetry.sdk.trace.sampling import ParentBased, Sampler, TraceIdRatioBased

ENV_TRACES_SAMPLER = "OTEL_TRACES_SAMPLER"


def sampler(ratio: float, environ: Mapping[str, str]) -> Sampler | None:
    """``None`` hands the choice to the SDK, which reads ``OTEL_TRACES_SAMPLER`` itself; otherwise a
    sampled parent is always kept and a root is kept at ``ratio``."""
    if environ.get(ENV_TRACES_SAMPLER, "").strip():
        return None
    return ParentBased(TraceIdRatioBased(ratio))
