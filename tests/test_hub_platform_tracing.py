"""The hub's platform spans (component tier) — a real ``build_hosted_app`` over an in-memory exporter."""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from blizzard import __version__
from blizzard.foundation.platform_tracing.handle import IPlatformTracing, build_platform_tracing
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.hub import app as hub_app
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.config import RUNNER_AUTH_ENFORCE, HubConfig
from blizzard.hub.domain.tracing.attributes import (
    PLATFORM_INSTRUMENTATION_SCOPE,
    PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
    resource_attributes,
)

pytestmark = pytest.mark.component

_ENDPOINT = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"}
_CALLER = "blizzard.caller"
_RUNNER_ID = "blizzard.runner.id"
_CHUNK_ID = "blizzard.chunk.id"


def _handle(config: HubConfig, exporter: InMemorySpanExporter) -> IPlatformTracing:
    return build_platform_tracing(
        replace(config.tracing, platform=True, platform_sample_ratio=1.0),
        _ENDPOINT,
        resource=resource_attributes(os.environ, __version__),
        scope=PLATFORM_INSTRUMENTATION_SCOPE,
        scope_version=PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
        exporter=exporter,
    )


def _config(tmp_path: Path) -> HubConfig:
    return hub_runtime.init_environment(tmp_path / "hub")


def _spans(handle: IPlatformTracing, exporter: InMemorySpanExporter) -> list:  # type: ignore[type-arg]
    handle.shutdown(5.0)
    return list(exporter.get_finished_spans())


def test_a_request_yields_a_route_template_server_span_with_parameterized_queries(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    config = _config(tmp_path)
    handle = _handle(config, exporter)
    app = hub_app.build_hosted_app(config, platform_tracing=handle)
    with TestClient(app) as client:
        client.get("/api/chunks/ch_abc?token=tok-secret-9")
    spans = _spans(handle, exporter)
    server = [s for s in spans if s.name == "GET /api/chunks/{chunk_id}"]
    assert len(server) == 1
    assert server[0].attributes[_CHUNK_ID] == "ch_abc"
    queries = [s for s in spans if s.parent is not None and s.parent.span_id == server[0].context.span_id]
    assert queries, "the request's store reads should be child spans"
    assert "tok-secret-9" not in repr([(s.name, dict(s.attributes or {})) for s in spans])


def test_a_sweep_pass_opens_its_own_root(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    config = _config(tmp_path)
    handle = _handle(config, exporter)
    app = hub_app.build_hosted_app(config, platform_tracing=handle)
    app.state.shutdown = asyncio.Event()
    sweeps = list(hub_app.Sweep.all(app))
    assert {sweep.name for sweep in sweeps} >= {"transcript_events", "work_closure", "work_item_materialization"}
    sweep = next(sweep for sweep in sweeps if sweep.name == "work_closure")

    async def one_pass() -> None:
        task = asyncio.create_task(sweep.run())
        await asyncio.sleep(0.5)
        app.state.shutdown.set()
        await task

    asyncio.run(one_pass())
    roots = [s for s in _spans(handle, exporter) if s.name == "sweep work_closure"]
    assert roots
    assert all(s.parent is None for s in roots)


def test_a_runner_bearer_stamps_caller_and_runner_id_and_a_forged_caller_header_changes_nothing(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    warn = TestClient(hub_app.build_hosted_app(config))
    assert warn.post("/api/fleet/runners", json={"runner_id": "runner-a", "workspace_id": "ws-a"}).status_code == 201
    token = warn.post("/api/runners/runner-a/enrollments").json()["token"]
    exporter = InMemorySpanExporter()
    enforced = replace(config, runner_auth_mode=RUNNER_AUTH_ENFORCE)
    handle = _handle(enforced, exporter)
    app = hub_app.build_hosted_app(enforced, platform_tracing=handle)
    with TestClient(app) as client:
        resp = client.get(
            "/api/fleet/queue/peek", headers={"Authorization": f"Bearer {token}", "blizzard.caller": "operator"}
        )
        assert resp.status_code == 200
    server = [s for s in _spans(handle, exporter) if s.name.startswith("GET /api/fleet/queue/peek")]
    assert len(server) == 1
    assert server[0].attributes[_CALLER] == "runner"
    assert server[0].attributes[_RUNNER_ID] == "runner-a"
    assert "operator" not in {s.attributes.get(_CALLER) for s in server}


def test_an_outbound_call_through_an_injected_oauth_client_is_an_httpx_client_span(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    config = _config(tmp_path)
    handle = _handle(config, exporter)
    oauth = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    hub_app.build_hosted_app(config, platform_tracing=handle, oauth_http_client=oauth)
    oauth.get("https://idp.example/token?code=tok-secret-9")
    spans = _spans(handle, exporter)
    client_spans = [s for s in spans if s.kind.name == "CLIENT" and "idp.example" in repr(dict(s.attributes or {}))]
    assert client_spans
    assert "tok-secret-9" not in repr([dict(s.attributes or {}) for s in spans])


def test_platform_off_with_an_endpoint_set_builds_no_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    config = _config(tmp_path)
    assert config.tracing == TracingConfig()
    app = hub_app.build_hosted_app(config)
    with TestClient(app) as client:
        client.get("/api/chunks/ch_x")
    from opentelemetry import trace

    assert type(trace.get_tracer_provider()).__name__ == "ProxyTracerProvider"


def test_a_route_without_a_chunk_id_carries_no_chunk_attribute(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    config = _config(tmp_path)
    handle = _handle(config, exporter)
    app = hub_app.build_hosted_app(config, platform_tracing=handle)
    with TestClient(app) as client:
        client.get("/api/health")
    server = [s for s in _spans(handle, exporter) if s.name == "GET /api/health"]
    assert len(server) == 1
    assert _CHUNK_ID not in (server[0].attributes or {})
