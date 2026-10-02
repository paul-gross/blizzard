"""The runner's platform spans (component tier) — the served app and the tick over an in-memory exporter."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from blizzard.foundation.platform_tracing.handle import IPlatformTracing, build_platform_tracing
from blizzard.foundation.tokens import TokenHash
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.domain.tracing.attributes import RUNNER_ID
from blizzard.runner.domain.tracing.platform import (
    PLATFORM_INSTRUMENTATION_SCOPE,
    PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
    TICK_STEP,
)
from blizzard.runner.loop.tick import tick
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    make_context,
    make_store,
    make_stores,
    no_retry_clock,
)

pytestmark = pytest.mark.component

_ENDPOINT = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"}
_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)
_LEASE_TOKEN = "lease-secret-1"
_HUB_TOKEN = "hub-secret-2"
_BODY = "body-secret-3"
_ROUTE_TOKEN = "route-secret-4"
_CALLER = "blizzard.caller"
_RUNNER = "r1"


def _handle(exporter: InMemorySpanExporter) -> IPlatformTracing:
    return build_platform_tracing(
        TracingConfig(platform=True, platform_sample_ratio=1.0),
        _ENDPOINT,
        resource={"service.name": "blizzard-runner"},
        scope=PLATFORM_INSTRUMENTATION_SCOPE,
        scope_version=PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
        stamped={RUNNER_ID: _RUNNER},
        exporter=exporter,
    )


def _attr(span: ReadableSpan, key: str) -> object:
    return (span.attributes or {}).get(key)


def _span_id(span: ReadableSpan) -> int | None:
    return None if span.context is None else span.context.span_id


def _parent_id(span: ReadableSpan) -> int | None:
    return None if span.parent is None else span.parent.span_id


def _finished(handle: IPlatformTracing, exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    handle.shutdown(5.0)
    return list(exporter.get_finished_spans())


def _seed_lease(store) -> None:  # type: ignore[no-untyped-def]
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
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


def _app(tmp_path: Path, handle: IPlatformTracing, seen: list[httpx.Request]):  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", hub_url="http://hub.local:8421")

    def hub(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"history": [], "migrations": [], "bounces": []})

    proxy = httpx.Client(
        transport=httpx.MockTransport(hub),
        base_url=config.hub_url,
        headers={"Authorization": f"Bearer {_HUB_TOKEN}"},
    )
    _seed_lease(store)
    handle.instrument_engine(store._engine)
    handle.instrument_client(proxy)
    app = create_app(
        config,
        runner_stores=make_stores(store),
        hub_proxy_client=proxy,
        hub_retry_clock=no_retry_clock(),
        platform_tracing=handle,
    )
    return app


def _history(client: TestClient) -> httpx.Response:
    return client.get(
        f"/api/leases/lease_1/history?route={_ROUTE_TOKEN}&note={_BODY}",
        headers={"X-Blizzard-Lease-Token": _LEASE_TOKEN},
    )


def test_a_lease_scoped_request_is_a_worker_server_span_and_a_hub_call_propagates_traceparent(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    handle = _handle(exporter)
    seen: list[httpx.Request] = []
    with TestClient(_app(tmp_path, handle, seen)) as client:
        assert _history(client).status_code == 200
    spans = _finished(handle, exporter)
    server = [s for s in spans if s.name.startswith("GET /api/leases/")]
    assert len(server) == 1
    assert _attr(server[0], _CALLER) == "worker"
    http_children = [s for s in spans if s.kind.name == "CLIENT" and s.parent is not None]
    assert any(_parent_id(s) == _span_id(server[0]) for s in http_children)
    assert seen and "traceparent" in seen[0].headers
    assert all(_attr(s, RUNNER_ID) == _RUNNER for s in spans)


def test_heartbeat_and_the_trace_receiver_path_yield_no_span_at_all(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    handle = _handle(exporter)
    with TestClient(_app(tmp_path, handle, [])) as client:
        beat = client.post("/api/heartbeat", json={"lease_id": "lease_1"})
        assert beat.status_code == 200
        client.post("/v1/traces", content=b"")
    assert _finished(handle, exporter) == []


def test_no_planted_secret_reaches_any_span(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    handle = _handle(exporter)
    with TestClient(_app(tmp_path, handle, [])) as client:
        _history(client)
        client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": "wrong"})
    spans = _finished(handle, exporter)
    assert spans
    emitted = repr(
        [
            (
                s.name,
                dict(s.attributes or {}),
                [(e.name, dict(e.attributes or {})) for e in s.events],
                dict(s.resource.attributes),
            )
            for s in spans
        ]
    )
    for secret in (_LEASE_TOKEN, TokenHash(_LEASE_TOKEN).hex, _HUB_TOKEN, _BODY, _ROUTE_TOKEN):
        assert secret not in emitted


class _CallingHub:
    """Delegates to a fake hub, making one traced outbound call first — a stand-in for the hub client."""

    def __init__(self, inner: FakeHub, client: httpx.Client) -> None:
        self._inner = inner
        self._client = client

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):  # type: ignore[no-untyped-def]
            self._client.get("http://hub.local/peek")
            return attr(*args, **kwargs)

        return call


def test_a_tick_is_a_root_with_one_child_per_step_and_hub_calls_under_their_step(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    handle = _handle(exporter)
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    handle.instrument_engine(store._engine)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    handle.instrument_client(client)
    ctx = make_context(
        store,
        hub=_CallingHub(FakeHub(), client),  # type: ignore[arg-type]
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=None, verdict=None),  # type: ignore[arg-type]
        probe=FakeProbe(alive=set()),
    )
    ctx = type(ctx)(**{**ctx.__dict__, "tracer": handle.tracer})
    tick(ctx)
    spans = _finished(handle, exporter)
    roots = [s for s in spans if s.name == "tick"]
    assert len(roots) == 1 and roots[0].parent is None
    steps = [s for s in spans if _attr(s, TICK_STEP)]
    names = [s.name for s in sorted(steps, key=lambda s: s.start_time or 0)]
    assert names == [
        "SpendCeiling",
        "Reap",
        "Resume",
        "Pull",
        "Fill",
        "Advance",
        "TranscriptDrain",
        "Retention",
        "ContextSample",
        "ExternalUsageSample",
    ]
    assert all(_parent_id(s) == _span_id(roots[0]) for s in steps)
    step_ids = {_span_id(s) for s in steps}
    hub_calls = [s for s in spans if s.kind.name == "CLIENT"]
    assert hub_calls and all(_parent_id(s) in step_ids or _parent_id(s) == _span_id(roots[0]) for s in hub_calls)
    assert all(_attr(s, RUNNER_ID) == _RUNNER for s in spans)
