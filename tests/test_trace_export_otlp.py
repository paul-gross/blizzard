"""The OTLP binding (component tier) — span records exported to an in-test OTLP/HTTP sink and decoded."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.internal.otlp import OtlpTraceExporter
from blizzard.foundation.trace_ids import DerivedContext, SpanRole, StepKey, chunk_span_id, lifetime_context
from blizzard.foundation.trace_spans import FinishedSpan, SpanEvent, SpanLink, SpanStatus
from blizzard.hub.domain.observability.tracing.attributes import INSTRUMENTATION_SCOPE, INSTRUMENTATION_SCOPE_VERSION
from tests.otlp_sink import OtlpSink, otlp_sink
from tests.trace_hub import trace_hub, transitioned_and_stopped

pytestmark = pytest.mark.component

_KEY = StepKey.attempt("ch_1", 2)
_PREVIOUS = StepKey.attempt("ch_1", 1)
_START = datetime(2026, 7, 13, 12, 0, 0, 123456, tzinfo=UTC)
_SCOPE = {"scope": INSTRUMENTATION_SCOPE, "scope_version": INSTRUMENTATION_SCOPE_VERSION}
_RESOURCE = {"service.name": "blizzard-hub", "service.version": "9.9.9", "blizzard.trace.schema_version": "1"}


def _nanos(at: datetime) -> int:
    return int(at.timestamp()) * 1_000_000_000 + at.microsecond * 1_000


def _records() -> tuple[FinishedSpan, FinishedSpan]:
    root = FinishedSpan(
        context=DerivedContext.of(_KEY, SpanRole.STEP),
        parent_span_id=None,
        name="build",
        start=_START,
        end=_START + timedelta(seconds=30),
        attributes={"blizzard.chunk.id": "ch_1", "blizzard.step.epoch": 2, "blizzard.work.refs": ("acme#1",)},
        status=SpanStatus.ERROR,
        events=(SpanEvent("gen_ai.invocation", _START + timedelta(seconds=10), {"gen_ai.usage.input_tokens": 7}),),
        links=(SpanLink(DerivedContext.of(_PREVIOUS, SpanRole.STEP), {"blizzard.link.reason": "retry"}),),
    )
    child = FinishedSpan(
        context=DerivedContext.of(_KEY, SpanRole.QUEUE),
        parent_span_id=root.context.span_id,
        name="queue",
        start=_START,
        end=_START + timedelta(seconds=5),
        attributes={"blizzard.cost.usd": 0.25, "blizzard.retry": True},
    )
    return root, child


@pytest.fixture
def sink(monkeypatch: pytest.MonkeyPatch) -> Iterator[OtlpSink]:
    with otlp_sink() as running:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", running.traces_endpoint)
        yield running


def test_the_sink_receives_derived_ids_parent_times_attributes_events_links_and_resource(sink: OtlpSink) -> None:
    root, child = _records()

    assert OtlpTraceExporter(resource=_RESOURCE, **_SCOPE).export([root, child]) is True

    [resource_spans] = sink.resource_spans()
    resource = {a.key: a.value.string_value for a in resource_spans.resource.attributes}
    assert {k: resource[k] for k in _RESOURCE} == _RESOURCE
    [scope_spans] = resource_spans.scope_spans
    assert scope_spans.scope.name == "blizzard.hub.fleet_spans"
    by_name = {s.name: s for s in scope_spans.spans}

    sent_root, sent_child = by_name["build"], by_name["queue"]
    trace = root.context.trace_id.to_bytes(16, "big")
    assert sent_root.trace_id == trace and sent_child.trace_id == trace
    assert sent_root.span_id == root.context.span_id.to_bytes(8, "big")
    assert sent_child.span_id == child.context.span_id.to_bytes(8, "big")
    assert sent_root.parent_span_id == b""
    assert sent_child.parent_span_id == sent_root.span_id
    assert sent_root.start_time_unix_nano == _nanos(root.start)
    assert sent_root.end_time_unix_nano == _nanos(root.end)
    assert sent_root.status.code == 2  # STATUS_CODE_ERROR

    attrs = {a.key: a.value for a in sent_root.attributes}
    assert attrs["blizzard.chunk.id"].string_value == "ch_1"
    assert attrs["blizzard.step.epoch"].int_value == 2
    assert [v.string_value for v in attrs["blizzard.work.refs"].array_value.values] == ["acme#1"]
    child_attrs = {a.key: a.value for a in sent_child.attributes}
    assert child_attrs["blizzard.cost.usd"].double_value == 0.25
    assert child_attrs["blizzard.retry"].bool_value is True

    [event] = sent_root.events
    assert event.name == "gen_ai.invocation"
    assert event.time_unix_nano == _nanos(_START + timedelta(seconds=10))
    assert event.attributes[0].key == "gen_ai.usage.input_tokens"
    [link] = sent_root.links
    previous = DerivedContext.of(_PREVIOUS, SpanRole.STEP)
    assert link.trace_id == previous.trace_id.to_bytes(16, "big")
    assert link.span_id == previous.span_id.to_bytes(8, "big")
    assert link.attributes[0].value.string_value == "retry"


def test_a_sink_answering_5xx_makes_export_return_false(sink: OtlpSink) -> None:
    sink.status = 500

    assert OtlpTraceExporter(resource=_RESOURCE, **_SCOPE).export(list(_records())) is False
    assert sink.requests == []


def test_the_sweep_tells_assembled_steps_to_the_sink(tmp_path: Path, sink: OtlpSink) -> None:
    exporter = OtlpTraceExporter(resource=_RESOURCE, **_SCOPE)
    hub, graph = trace_hub(tmp_path, trace_exporter=exporter, tracing=TracingConfig(settle_seconds=0))
    sweep = hub.services.trace_export
    assert sweep is not None
    sweep.sweep()
    moved, stopped = transitioned_and_stopped(hub, graph, 1)

    sweep.sweep()

    spans = sink.spans()
    steps = [s for s in spans if s.name.startswith("step ")]
    expected = {DerivedContext.of(StepKey.attempt(c, 1), SpanRole.STEP).span_id for c in (moved, stopped)}
    assert {int.from_bytes(s.span_id, "big") for s in steps} == expected
    [work] = [s for s in spans if s.name == "chunk work"]
    assert (work.parent_span_id, int.from_bytes(work.span_id, "big")) == (b"", chunk_span_id(stopped))
    [lifetime] = [s for s in spans if s.name == "chunk"]
    assert lifetime.span_id == lifetime_context(stopped).span_id.to_bytes(8, "big")
    assert {s.parent_span_id for s in steps} == {chunk_span_id(c).to_bytes(8, "big") for c in (moved, stopped)}
    services = {
        a.value.string_value for rs in sink.resource_spans() for a in rs.resource.attributes if a.key == "service.name"
    }
    assert services == {"blizzard-hub", "blizzard-chunk"}


def test_a_span_naming_a_service_leaves_under_a_resource_of_its_own(sink: OtlpSink) -> None:
    root, child = _records()
    lifetime = replace(child, service_name="blizzard-chunk")

    assert OtlpTraceExporter(resource=_RESOURCE, **_SCOPE).export([root, lifetime]) is True

    by_service = {
        {a.key: a.value.string_value for a in rs.resource.attributes}["service.name"]: rs
        for rs in sink.resource_spans()
    }
    assert set(by_service) == {"blizzard-hub", "blizzard-chunk"}
    named = {a.key: a.value.string_value for a in by_service["blizzard-chunk"].resource.attributes}
    assert {k: named[k] for k in _RESOURCE if k != "service.name"} == {
        k: v for k, v in _RESOURCE.items() if k != "service.name"
    }
    for resource_spans in by_service.values():
        [scope_spans] = resource_spans.scope_spans
        assert (scope_spans.scope.name, scope_spans.scope.version) == tuple(_SCOPE.values())
    assert [s.name for s in by_service["blizzard-chunk"].scope_spans[0].spans] == ["queue"]
