"""Platform tracing's foundation (unit tier) — enablement, sampling, redaction, exclusion, and import laziness."""

from __future__ import annotations

import json
import subprocess
import sys

import httpx
import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import Decision
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, set_span_in_context
from sqlalchemy import create_engine, text

from blizzard.foundation.platform_tracing.attributes import CALLER, annotate_caller
from blizzard.foundation.platform_tracing.exclusion import is_excluded
from blizzard.foundation.platform_tracing.handle import (
    DisabledPlatformTracing,
    build_platform_tracing,
    platform_tracing_enabled,
)
from blizzard.foundation.platform_tracing.internal.redaction import RedactingExporter
from blizzard.foundation.platform_tracing.internal.sampling import sampler
from blizzard.foundation.trace_export.config import TracingConfig

pytestmark = pytest.mark.unit

_ENDPOINT = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"}
_RUNNER_ID = "blizzard.runner.id"
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
        stamped={_RUNNER_ID: "r-1"},
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


def test_importing_the_package_with_tracing_off_loads_no_sdk_or_instrumentation() -> None:
    code = (
        "import sys, json\n"
        "from blizzard.foundation.platform_tracing import attributes, exclusion, handle, semconv, tracer\n"
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
