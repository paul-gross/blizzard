"""Runner and hub platform spans chained across both daemons (component tier) — the runner app's hub
proxy pointed at the real hub app, each daemon on its own in-memory exporter."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from blizzard import __version__
from blizzard.foundation.platform_tracing.handle import IPlatformTracing, build_platform_tracing
from blizzard.foundation.tokens import TokenHash
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_ids import StepKey, step_root
from blizzard.hub import app as hub_app
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.domain.tracing.attributes import (
    PLATFORM_INSTRUMENTATION_SCOPE as HUB_SCOPE,
)
from blizzard.hub.domain.tracing.attributes import (
    PLATFORM_INSTRUMENTATION_SCOPE_VERSION as HUB_SCOPE_VERSION,
)
from blizzard.hub.domain.tracing.attributes import resource_attributes
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.domain.tracing.platform import (
    PLATFORM_INSTRUMENTATION_SCOPE as RUNNER_SCOPE,
)
from blizzard.runner.domain.tracing.platform import (
    PLATFORM_INSTRUMENTATION_SCOPE_VERSION as RUNNER_SCOPE_VERSION,
)
from tests.runner_fakes import make_store, make_stores, no_retry_clock

pytestmark = pytest.mark.component

_ENDPOINT = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"}
_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)
_LEASE_TOKEN = "lease-secret-1"
_RUNNER = "runner-a"


def _span_id(span: ReadableSpan) -> int:
    assert span.context is not None
    return span.context.span_id


def _parent_id(span: ReadableSpan) -> int | None:
    return None if span.parent is None else span.parent.span_id


def _finished(handle: IPlatformTracing, exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    handle.shutdown(5.0)
    return list(exporter.get_finished_spans())


def _only(spans: list[ReadableSpan], kind: str, prefix: str) -> ReadableSpan:
    found = [s for s in spans if s.kind.name == kind and s.name.startswith(prefix)]
    assert len(found) == 1, [(s.kind.name, s.name) for s in spans]
    return found[0]


def test_a_worker_read_chains_runner_and_hub_spans_under_the_step_root(tmp_path: Path) -> None:
    hub_exporter = InMemorySpanExporter()
    hub_config = hub_runtime.init_environment(tmp_path / "hub")
    hub_handle = build_platform_tracing(
        replace(hub_config.tracing, platform=True, platform_sample_ratio=1.0),
        _ENDPOINT,
        resource=resource_attributes(os.environ, __version__),
        scope=HUB_SCOPE,
        scope_version=HUB_SCOPE_VERSION,
        exporter=hub_exporter,
    )
    runner_exporter = InMemorySpanExporter()
    runner_handle = build_platform_tracing(
        TracingConfig(platform=True, platform_sample_ratio=1.0),
        _ENDPOINT,
        resource={"service.name": "blizzard-runner"},
        scope=RUNNER_SCOPE,
        scope_version=RUNNER_SCOPE_VERSION,
        exporter=runner_exporter,
    )

    with TestClient(hub_app.build_hosted_app(hub_config, platform_tracing=hub_handle)) as hub:
        assert hub.post("/api/fleet/runners", json={"runner_id": _RUNNER, "workspace_id": "ws-a"}).status_code == 201
        bearer = hub.post(f"/api/runners/{_RUNNER}/enrollments").json()["token"]
        chunk_id = hub.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()["chunk_id"]
        hub.headers["Authorization"] = f"Bearer {bearer}"
        hub_exporter.clear()

        db_url = f"sqlite:///{tmp_path / 'runner.db'}"
        store = make_store(db_url)
        store.record_lease(
            NewLease(
                lease_id="lease_1",
                chunk_id=chunk_id,
                graph_id="gr_1",
                node_id="nd_build",
                node_name="build",
                epoch=1,
                runner_id=_RUNNER,
                retries_max=2,
                created_at=_NOW,
            )
        )
        store.record_lease_token("lease_1", TokenHash(_LEASE_TOKEN).hex, _NOW)
        runner_handle.instrument_engine(store._engine)
        runner_handle.instrument_client(hub)
        runner = create_app(
            RunnerConfig(root=tmp_path, db_url=db_url, hub_url=str(hub.base_url)),
            runner_stores=make_stores(store),
            hub_proxy_client=hub,
            hub_retry_clock=no_retry_clock(),
            platform_tracing=runner_handle,
        )
        root = step_root(StepKey.attempt(chunk_id, 1))
        with TestClient(runner) as worker:
            read = worker.get(
                "/api/leases/lease_1/history",
                headers={
                    "X-Blizzard-Lease-Token": _LEASE_TOKEN,
                    "traceparent": f"00-{root.trace_id:032x}-{root.span_id:016x}-01",
                },
            )
            assert read.status_code == 200, read.text

    runner_spans = _finished(runner_handle, runner_exporter)
    hub_spans = _finished(hub_handle, hub_exporter)
    runner_server = _only(runner_spans, "SERVER", "GET /api/leases/")
    runner_client = _only(runner_spans, "CLIENT", "GET")
    hub_server = _only(hub_spans, "SERVER", "GET /api/fleet/chunks/")
    hub_queries = [s for s in hub_spans if _parent_id(s) == _span_id(hub_server)]

    assert _parent_id(runner_server) == root.span_id
    assert _parent_id(runner_client) == _span_id(runner_server)
    assert _parent_id(hub_server) == _span_id(runner_client)
    assert hub_queries, "the hub request's store reads should be child spans"
    chained = [runner_server, runner_client, hub_server, *hub_queries]
    assert {s.context.trace_id for s in chained if s.context is not None} == {root.trace_id}
