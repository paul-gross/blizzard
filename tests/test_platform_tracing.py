"""Platform tracing's foundation (unit tier) — enablement, sampling, redaction, exclusion, and import laziness."""

from __future__ import annotations

import json
import subprocess
import sys

import httpx
import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import Decision
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import (
    Link,
    NonRecordingSpan,
    SpanContext,
    SpanKind,
    Status,
    StatusCode,
    TraceFlags,
    set_span_in_context,
)
from sqlalchemy import create_engine, text

from blizzard.foundation.platform_tracing.attributes import CALLER, annotate_caller
from blizzard.foundation.platform_tracing.handle import (
    DisabledPlatformTracing,
    IPlatformTracing,
    build_platform_tracing,
    platform_tracing_enabled,
)
from blizzard.foundation.platform_tracing.internal.redaction import RedactingExporter
from blizzard.foundation.platform_tracing.internal.sampling import sampler
from blizzard.foundation.platform_tracing.tracer import NoopPlatformTracer
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_ids import DerivedContext, SpanRole, StepKey
from blizzard.runner.tracing.exclusion import is_excluded

pytestmark = pytest.mark.unit

_ENDPOINT = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"}
_RUNNER_ID = "blizzard.runner.id"
_RUNNER_NAME = "blizzard.runner.name"
_RESOURCE = {"service.name": "blizzard-test"}


@pytest.mark.parametrize(
    ("platform", "environ", "enabled"),
    [(True, _ENDPOINT, True), (True, {}, False), (False, _ENDPOINT, False), (False, {}, False)],
)
def test_platform_tracing_is_on_only_when_both_switches_are(
    platform: bool, environ: dict[str, str], enabled: bool
) -> None:
    assert platform_tracing_enabled(TracingConfig(platform=platform), environ) is enabled


def test_a_rejected_protocol_turns_platform_tracing_off() -> None:
    environ = {**_ENDPOINT, "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"}
    assert not platform_tracing_enabled(TracingConfig(platform=True), environ)


def test_the_disabled_handle_keeps_fastapi_tracing_off() -> None:
    handle = build_platform_tracing(TracingConfig(), _ENDPOINT, resource=_RESOURCE, scope="s", scope_version="1")
    assert isinstance(handle, DisabledPlatformTracing)
    assert handle.fastapi_telemetry() == {"tracing": False, "auto_configure": False}
    with handle.tracer.root("tick"), handle.tracer.child("step"):
        pass


def _root_decision(ratio: float, trace_id: int) -> Decision:
    chosen = sampler(ratio, {})
    assert chosen is not None
    return chosen.should_sample(None, trace_id, "root").decision


def test_roots_sample_at_the_configured_ratio() -> None:
    ids = range(1, 2**64, 2**64 // 2000)
    kept = sum(_root_decision(0.25, trace_id) is Decision.RECORD_AND_SAMPLE for trace_id in ids)
    assert 0.2 < kept / len(ids) < 0.3
    assert all(_root_decision(0.0, trace_id) is Decision.DROP for trace_id in ids)
    assert all(_root_decision(1.0, trace_id) is Decision.RECORD_AND_SAMPLE for trace_id in ids)


def test_a_sampled_parent_is_always_kept() -> None:
    chosen = sampler(0.0, {})
    assert chosen is not None
    parent = NonRecordingSpan(SpanContext(1, 2, is_remote=True, trace_flags=TraceFlags(TraceFlags.SAMPLED)))
    result = chosen.should_sample(set_span_in_context(parent), 1, "child")
    assert result.decision is Decision.RECORD_AND_SAMPLE


def test_otel_traces_sampler_replaces_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "always_off")
    environ = {**_ENDPOINT, "OTEL_TRACES_SAMPLER": "always_off"}
    assert sampler(1.0, environ) is None
    exporter = InMemorySpanExporter()
    handle = build_platform_tracing(
        TracingConfig(platform=True, platform_sample_ratio=1.0),
        environ,
        resource=_RESOURCE,
        scope="s",
        scope_version="1",
        exporter=exporter,
    )
    with handle.tracer.root("tick"):
        pass
    handle.shutdown(5)
    assert exporter.get_finished_spans() == ()


def _span(attributes: dict[str, str]) -> ReadableSpan:
    return ReadableSpan(name="GET /x", context=SpanContext(1, 2, is_remote=False), attributes=attributes)


def test_the_redacting_stage_copies_every_other_span_field_unchanged() -> None:
    inner = InMemorySpanExporter()
    parent = SpanContext(1, 9, is_remote=True)
    original = ReadableSpan(
        name="GET /x",
        context=SpanContext(1, 2, is_remote=False),
        parent=parent,
        resource=Resource({"service.name": "svc"}),
        attributes={"url.full": "https://hub.example/x?token=planted", "kept": "yes"},
        events=(Event("boom", {"k": "v"}, 15),),
        links=(Link(SpanContext(1, 3, is_remote=False)),),
        kind=SpanKind.CLIENT,
        status=Status(StatusCode.ERROR, "failed"),
        start_time=100,
        end_time=250,
        instrumentation_scope=InstrumentationScope("scope.name", "7"),
    )

    RedactingExporter(inner).export([original])

    (exported,) = inner.get_finished_spans()
    for field in (
        "name",
        "context",
        "parent",
        "resource",
        "events",
        "links",
        "kind",
        "status",
        "start_time",
        "end_time",
        "instrumentation_scope",
    ):
        assert getattr(exported, field) == getattr(original, field), field
    assert dict(exported.attributes or {}) == {"url.full": "https://hub.example/x", "kept": "yes"}


def test_the_built_pipeline_stamps_its_resource_on_every_span() -> None:
    exporter = InMemorySpanExporter()
    handle = build_platform_tracing(
        TracingConfig(platform=True, platform_sample_ratio=1.0),
        _ENDPOINT,
        resource={"service.name": "blizzard-test", "deployment.environment": "unit"},
        scope="blizzard.test",
        scope_version="2",
        exporter=exporter,
    )
    with handle.tracer.root("tick"):
        pass
    handle.shutdown(5)

    (span,) = exporter.get_finished_spans()
    assert span.resource.attributes["service.name"] == "blizzard-test"
    assert span.resource.attributes["deployment.environment"] == "unit"
    assert span.instrumentation_scope is not None
    assert (span.instrumentation_scope.name, span.instrumentation_scope.version) == ("blizzard.test", "2")


def test_the_redacting_stage_strips_a_planted_query_token() -> None:
    inner = InMemorySpanExporter()
    planted = {
        "url.full": "https://hub.example/api/chunks/ch_1?token=planted#frag",
        "url.query": "token=planted",
        "http.url": "https://hub.example/api/chunks/ch_1?token=planted",
        "http.target": "/api/chunks/ch_1?token=planted",
        "url.path": "/api/chunks/ch_1",
    }
    assert RedactingExporter(inner).export([_span(planted)]) is SpanExportResult.SUCCESS
    (exported,) = inner.get_finished_spans()
    assert dict(exported.attributes or {}) == {
        "url.full": "https://hub.example/api/chunks/ch_1",
        "http.url": "https://hub.example/api/chunks/ch_1",
        "http.target": "/api/chunks/ch_1",
        "url.path": "/api/chunks/ch_1",
    }
    assert "planted" not in json.dumps(dict(exported.attributes or {}))


@pytest.mark.parametrize(
    ("method", "path", "excluded"),
    [
        ("POST", "/api/heartbeat", True),
        ("POST", "/v1/traces", True),
        ("GET", "/v1/traces", True),
        ("POST", "/api/otlp/v1/traces", True),
        ("POST", "/v1/traces/", True),
        ("post", "/api/heartbeat", True),
        ("GET", "/api/heartbeat", False),
        ("POST", "/api/runners/r1/heartbeat", False),
        ("GET", "/api/chunks/ch_1", False),
        ("POST", "/v1/traces/extra", False),
        ("GET", "/", False),
    ],
)
def test_the_exclusion_predicate_matches_heartbeat_and_v1_traces_only(method: str, path: str, excluded: bool) -> None:
    assert is_excluded({"type": "http", "method": method, "path": path}) is excluded


def _span_id(span: ReadableSpan) -> int:
    assert span.context is not None
    return span.context.span_id


def _parent_id(span: ReadableSpan) -> int | None:
    return None if span.parent is None else span.parent.span_id


def test_the_enabled_pipeline_exports_roots_children_queries_and_client_calls() -> None:
    exporter = InMemorySpanExporter()
    handle = build_platform_tracing(
        TracingConfig(platform=True, platform_sample_ratio=1.0),
        _ENDPOINT,
        resource=_RESOURCE,
        scope="blizzard.test",
        scope_version="1",
        stamp=lambda: {_RUNNER_ID: "r-1"},
        exporter=exporter,
    )
    engine = create_engine("sqlite://")
    handle.instrument_engine(engine)
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(204)))
    handle.instrument_client(client)
    with handle.tracer.root("tick"), handle.tracer.child("step", {"blizzard.tick.step": "Step"}):
        annotate_caller("worker")
        with engine.connect() as connection:
            connection.execute(text("SELECT :value"), {"value": "bound-secret"})
        client.get("https://hub.example/api/x?token=planted")
    with handle.suppressed(), engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    handle.shutdown(5)

    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert spans["tick"].parent is None
    step = spans["step"]
    assert _parent_id(step) == _span_id(spans["tick"])
    assert (step.attributes or {})[CALLER] == "worker"
    children = [s for s in spans.values() if _parent_id(s) == _span_id(step)]
    assert len(children) == 2
    assert all((s.attributes or {})[_RUNNER_ID] == "r-1" for s in spans.values())
    assert len(spans) == 4
    dumped = json.dumps([dict(s.attributes or {}) for s in spans.values()])
    assert "planted" not in dumped and "bound-secret" not in dumped


def test_a_span_started_before_the_stamp_answers_is_never_exported_and_later_spans_carry_its_latest() -> None:
    exporter = InMemorySpanExporter()
    stamped: list[dict[str, str] | None] = [None]
    handle = build_platform_tracing(
        TracingConfig(platform=True, platform_sample_ratio=1.0),
        _ENDPOINT,
        resource=_RESOURCE,
        scope="blizzard.test",
        scope_version="1",
        stamp=lambda: stamped[0],
        exporter=exporter,
    )
    with handle.tracer.root("before"), handle.tracer.child("before-child"):
        pass
    with handle.tracer.root("straddling"):
        stamped[0] = {_RUNNER_ID: "rn_1", _RUNNER_NAME: "runner-a"}
    with handle.tracer.root("registered"):
        pass
    stamped[0] = {_RUNNER_ID: "rn_1", _RUNNER_NAME: "runner-b"}
    with handle.tracer.root("renamed"):
        pass
    handle.shutdown(5)

    spans = {span.name: dict(span.attributes or {}) for span in exporter.get_finished_spans()}
    assert set(spans) == {"registered", "renamed"}
    assert (spans["registered"][_RUNNER_ID], spans["registered"][_RUNNER_NAME]) == ("rn_1", "runner-a")
    assert (spans["renamed"][_RUNNER_ID], spans["renamed"][_RUNNER_NAME]) == ("rn_1", "runner-b")


def test_importing_the_package_with_tracing_off_loads_no_sdk_or_instrumentation() -> None:
    code = (
        "import sys, json\n"
        "from blizzard.foundation.platform_tracing import attributes, handle, semconv, tracer\n"
        "from blizzard.runner.tracing.exclusion import is_excluded\n"
        "from blizzard.foundation.trace_export.config import TracingConfig\n"
        "h = handle.build_platform_tracing(TracingConfig(), {'OTEL_EXPORTER_OTLP_ENDPOINT': 'http://x:4318'},"
        " resource={}, scope='s', scope_version='1')\n"
        "h.fastapi_telemetry()\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    loaded = json.loads(result.stdout)
    heavy = [m for m in loaded if m.startswith(("opentelemetry.sdk", "opentelemetry.instrumentation"))]
    assert not heavy, heavy


def test_a_raising_span_records_failure_but_never_the_exception_message() -> None:
    exporter = InMemorySpanExporter()
    handle = build_platform_tracing(
        TracingConfig(platform=True, platform_sample_ratio=1.0),
        _ENDPOINT,
        resource=_RESOURCE,
        scope="s",
        scope_version="1",
        exporter=exporter,
    )
    with pytest.raises(ValueError), handle.tracer.root("tick"):
        raise ValueError("token=s3cret")
    handle.shutdown(5.0)
    (span,) = exporter.get_finished_spans()
    assert span.status.status_code.name == "ERROR"
    assert span.status.description is None
    assert "s3cret" not in repr((dict(span.attributes or {}), span.events))


_DERIVED = DerivedContext.of(StepKey.attempt("ch_1", 3), SpanRole.STEP)


def _ratio_handle(exporter: InMemorySpanExporter, ratio: float) -> IPlatformTracing:
    return build_platform_tracing(
        TracingConfig(platform=True, platform_sample_ratio=ratio),
        _ENDPOINT,
        resource=_RESOURCE,
        scope="s",
        scope_version="1",
        exporter=exporter,
    )


@pytest.mark.parametrize("ratio", [0.0, 1.0])
def test_a_span_opened_under_a_derived_context_is_its_sampled_child_whatever_the_ratio(ratio: float) -> None:
    exporter = InMemorySpanExporter()
    handle = _ratio_handle(exporter, ratio)
    with handle.tracer.root("ambient"), handle.tracer.under(_DERIVED), handle.tracer.child("call"):
        pass
    with handle.tracer.root("after"):
        pass
    handle.shutdown(5.0)
    spans = {span.name: span for span in exporter.get_finished_spans()}
    call = spans["call"]
    assert call.context is not None and call.context.trace_id == _DERIVED.trace_id
    assert call.parent is not None and call.parent.span_id == _DERIVED.span_id and call.parent.is_remote
    assert ("after" in spans) == (ratio == 1.0)


def test_under_opens_no_span_of_its_own() -> None:
    exporter = InMemorySpanExporter()
    handle = _ratio_handle(exporter, 1.0)
    with handle.tracer.under(_DERIVED):
        pass
    handle.shutdown(5.0)
    assert exporter.get_finished_spans() == ()


def test_link_adds_the_derived_context_to_the_current_span() -> None:
    exporter = InMemorySpanExporter()
    handle = _ratio_handle(exporter, 1.0)
    handle.tracer.link(_DERIVED)
    with handle.tracer.root("request"):
        handle.tracer.link(_DERIVED)
    handle.shutdown(5.0)
    (span,) = exporter.get_finished_spans()
    assert [(link.context.trace_id, link.context.span_id) for link in span.links] == [
        (_DERIVED.trace_id, _DERIVED.span_id)
    ]


def test_the_noop_tracer_under_and_link_do_nothing() -> None:
    tracer = NoopPlatformTracer()
    with tracer.under(_DERIVED):
        tracer.link(_DERIVED)
