"""The OTLP binding's span mapping (unit tier), through an injected in-memory span exporter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from blizzard.foundation.trace_export.internal.otlp import OtlpTraceExporter
from blizzard.foundation.trace_ids import DerivedContext, SpanRole, StepKey
from blizzard.foundation.trace_spans import FinishedSpan, SpanEvent, SpanLink, SpanStatus

_KEY = StepKey.attempt("ch_1", 2)
_PREVIOUS = StepKey.attempt("ch_1", 1)
_START = datetime(2026, 7, 13, 12, 0, 0, 123456, tzinfo=UTC)


def test_spans_map_to_readable_spans_with_ids_parent_status_events_links_and_resource() -> None:
    root = FinishedSpan(
        context=DerivedContext.of(_KEY, SpanRole.STEP),
        parent_span_id=None,
        name="build",
        start=_START,
        end=_START + timedelta(seconds=30),
        attributes={"blizzard.chunk.id": "ch_1"},
        status=SpanStatus.ERROR,
        events=(SpanEvent("gen_ai.invocation", _START + timedelta(seconds=10), {"n": 7}),),
        links=(SpanLink(DerivedContext.of(_PREVIOUS, SpanRole.STEP), {"reason": "retry"}),),
    )
    child = FinishedSpan(
        context=DerivedContext.of(_KEY, SpanRole.QUEUE),
        parent_span_id=root.context.span_id,
        name="queue",
        start=_START,
        end=_START + timedelta(seconds=5),
        attributes={},
        service_name="other-service",
    )
    memory = InMemorySpanExporter()

    exporter = OtlpTraceExporter(resource={"service.name": "hub"}, scope="s", scope_version="1", exporter=memory)
    assert exporter.export([root, child]) is True

    sent_root, sent_child = memory.get_finished_spans()
    assert sent_root.context is not None
    assert sent_root.context.trace_id == root.context.trace_id
    assert sent_root.context.span_id == root.context.span_id
    assert sent_root.parent is None
    assert sent_root.status.status_code is StatusCode.ERROR
    assert sent_root.resource.attributes["service.name"] == "hub"
    [event] = sent_root.events
    assert event.name == "gen_ai.invocation"
    assert dict(event.attributes or {}) == {"n": 7}
    [link] = sent_root.links
    assert link.context is not None
    previous = DerivedContext.of(_PREVIOUS, SpanRole.STEP)
    assert (link.context.trace_id, link.context.span_id) == (previous.trace_id, previous.span_id)
    assert sent_child.parent is not None
    assert sent_child.parent.span_id == root.context.span_id
    assert sent_child.resource.attributes["service.name"] == "other-service"
