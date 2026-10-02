from __future__ import annotations

from opentelemetry.trace import SpanContext, TraceFlags

from blizzard.foundation.trace_ids import DerivedContext


def span_context(derived: DerivedContext, *, remote: bool = False) -> SpanContext:
    return SpanContext(derived.trace_id, derived.span_id, is_remote=remote, trace_flags=TraceFlags(derived.trace_flags))
