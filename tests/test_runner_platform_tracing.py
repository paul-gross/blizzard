"""The runner's platform spans (component tier) — the served app and the tick over an in-memory exporter."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient
from google.protobuf.json_format import Parse
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.platform_tracing.handle import IPlatformTracing, build_platform_tracing
from blizzard.foundation.tokens import TokenHash
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.foundation.trace_ids import DerivedContext, StepKey, step_root, trace_id
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.leases import LeaseRecord, NewLease
from blizzard.runner.domain.tracing.attributes import RUNNER_ID
from blizzard.runner.domain.tracing.platform import (
    PLATFORM_INSTRUMENTATION_SCOPE,
    PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
    TICK_STEP,
)
from blizzard.runner.domain.tracing.receiver import MAX_BODY_BYTES
from blizzard.runner.domain.tracing.receiver_limits import ReceiverCounter, SpanRateLimiter
from blizzard.runner.domain.tracing.status import LeaseTraceStatusReader
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.outbound import OutboundFacts
from blizzard.runner.loop.steps import Advance
from blizzard.runner.loop.tick import tick
from blizzard.wire.chunk import ChunkDecisionStatusView, ChunkStatusView
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    make_context,
    make_envelope,
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


def _app(  # type: ignore[no-untyped-def]
    tmp_path: Path,
    handle: IPlatformTracing,
    seen: list[httpx.Request],
    counter: ReceiverCounter | None = None,
    limiter: SpanRateLimiter | None = None,
    worker_programs: bool = False,
    services: dict[str, str] | None = None,
):
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(
        root=tmp_path,
        db_url=f"sqlite:///{tmp_path / 'runner.db'}",
        hub_url="http://hub.local:8421",
        tracing=TracingConfig(worker_programs=worker_programs, worker_program_services=services or {}),
    )

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
        span_limiter=limiter,
        receiver_counter=counter,
        trace_status=LeaseTraceStatusReader(
            settings=TracingSettings.of(_ENDPOINT),
            leases=make_stores(store).lease_traces,
            clock=FixedClock(_NOW),
            receiver=counter,
        ),
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
    """Delegates to a fake hub, first making one traced call whose path names the method — a stand-in for the hub client."""

    def __init__(self, inner: FakeHub, client: httpx.Client) -> None:
        self._inner = inner
        self._client = client

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):  # type: ignore[no-untyped-def]
            self._client.get(f"http://hub.local/{name}")
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


# --- The OTLP receiver --------------------------------------------------------------------------------

_TOKEN = {"X-Blizzard-Lease-Token": _LEASE_TOKEN}
_SPAN = 0x00F067AA0BA902B7


def _export(trace: int, *, scope: str = "blizzard.cli", extra: dict[str, object] | None = None) -> dict[str, object]:
    attributes = [
        {"key": "blizzard.cli.command", "value": {"stringValue": "artifact create"}},
        {"key": "blizzard.caller", "value": {"stringValue": "operator"}},
        {"key": "secret", "value": {"stringValue": "s3cret"}},
    ]
    span = {
        "traceId": f"{trace:032x}",
        "spanId": f"{_SPAN:016x}",
        "name": "blizzard artifact create",
        "kind": 3,
        "startTimeUnixNano": "1000",
        "endTimeUnixNano": "2000",
        "attributes": attributes,
        **(extra or {}),
    }
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "planted"}}]},
                "scopeSpans": [{"scope": {"name": scope, "version": "1"}, "spans": [span]}],
            }
        ]
    }


def _own_trace() -> int:
    return trace_id(StepKey.attempt("ch_1", 1))


def _protobuf(document: dict[str, object]) -> bytes:
    import base64

    for resource in document["resourceSpans"]:  # type: ignore[attr-defined]
        for scope in resource["scopeSpans"]:
            for span in scope["spans"]:
                for key in ("traceId", "spanId"):
                    span[key] = base64.b64encode(bytes.fromhex(span[key])).decode()
    return Parse(json.dumps(document), ExportTraceServiceRequest()).SerializeToString()


def _post_json(client: TestClient, document: dict[str, object], headers: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    return client.post(
        "/v1/traces",
        content=json.dumps(document),
        headers={"Content-Type": "application/json", **(_TOKEN if headers is None else headers)},
    )


def _worker_spans(spans: list[ReadableSpan]) -> list[ReadableSpan]:
    return [s for s in spans if s.instrumentation_scope is not None and s.instrumentation_scope.name == "blizzard.cli"]


def test_an_otlp_json_post_with_the_lease_token_exports_a_rebuilt_span(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    handle = _handle(exporter)
    with TestClient(_app(tmp_path, handle, [])) as client:
        response = _post_json(client, _export(_own_trace()))
        assert response.status_code == 200
        assert "partialSuccess" not in response.json()
    (span,) = _worker_spans(_finished(handle, exporter))
    assert span.resource.attributes["service.name"] == "blizzard-cli"
    assert dict(span.attributes or {}) == {
        "blizzard.cli.command": "artifact create",
        _CALLER: "worker",
        "blizzard.chunk.id": "ch_1",
        "blizzard.lease.id": "lease_1",
    }
    assert span.context is not None and span.context.trace_id == _own_trace()


def test_an_otlp_protobuf_post_exports_the_same_span(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    handle = _handle(exporter)
    with TestClient(_app(tmp_path, handle, [])) as client:
        response = client.post(
            "/v1/traces",
            content=_protobuf(_export(_own_trace())),
            headers={"Content-Type": "application/x-protobuf", **_TOKEN},
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/x-protobuf"
        assert ExportTraceServiceResponse.FromString(response.content).partial_success.rejected_spans == 0
    (span,) = _worker_spans(_finished(handle, exporter))
    assert span.resource.attributes["service.name"] == "blizzard-cli"
    assert (span.attributes or {})[_CALLER] == "worker"


@pytest.mark.parametrize("headers", [{}, {"X-Blizzard-Lease-Token": "wrong"}])
def test_a_request_without_a_valid_lease_token_is_refused_403(tmp_path: Path, headers: dict[str, str]) -> None:
    exporter = InMemorySpanExporter()
    handle = _handle(exporter)
    with TestClient(_app(tmp_path, handle, [])) as client:
        assert _post_json(client, _export(_own_trace()), headers).status_code == 403
    assert _worker_spans(_finished(handle, exporter)) == []


def test_a_closed_leases_token_is_refused_403(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    handle = _handle(exporter)
    app = _app(tmp_path, handle, [])
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="done", closed_at=_NOW)
    with TestClient(app) as client:
        assert _post_json(client, _export(_own_trace())).status_code == 403


def test_another_chunks_span_is_dropped_reported_and_counted(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    handle = _handle(exporter)
    counter = ReceiverCounter()
    with TestClient(_app(tmp_path, handle, [], counter)) as client:
        foreign = trace_id(StepKey.attempt("ch_other", 1))
        response = _post_json(client, _export(foreign))
        assert response.status_code == 200
        assert int(response.json()["partialSuccess"]["rejectedSpans"]) == 1
        assert _post_json(client, _export(_own_trace())).status_code == 200
        status = client.get("/api/traces/status").json()
    assert status["receiver"] == {"accepted_spans": 1, "dropped_spans": 1}
    assert len(_worker_spans(_finished(handle, exporter))) == 1


def test_a_foreign_scope_is_dropped(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    handle = _handle(exporter)
    with TestClient(_app(tmp_path, handle, [])) as client:
        response = _post_json(client, _export(_own_trace(), scope="some.library"))
        assert int(response.json()["partialSuccess"]["rejectedSpans"]) == 1
    assert _finished(handle, exporter) == []


def test_worker_programs_keeps_another_scope_inside_the_step_and_still_drops_another_chunks(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    handle = _handle(exporter)
    with TestClient(_app(tmp_path, handle, [], worker_programs=True)) as client:
        ours = _post_json(
            client,
            _export(
                _own_trace(),
                scope="some.library",
                extra={"attributes": [{"key": "db.system", "value": {"stringValue": "sqlite"}}]},
            ),
        )
        assert "partialSuccess" not in ours.json()
        foreign = trace_id(StepKey.attempt("ch_other", 1))
        theirs = _post_json(client, _export(foreign, scope="some.library"))
        assert int(theirs.json()["partialSuccess"]["rejectedSpans"]) == 1
    (span,) = _finished(handle, exporter)
    assert span.resource.attributes["service.name"] == "blizzard-worker-program"
    assert dict(span.attributes or {})["db.system"] == "sqlite"
    assert dict(span.attributes or {})["blizzard.lease.id"] == "lease_1"


def test_a_mapped_scope_leaves_under_its_service_name_and_the_rest_keep_theirs(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    handle = _handle(exporter)
    app = _app(tmp_path, handle, [], worker_programs=True, services={"winter_cli": "winter-blizzard"})
    scopes = ("winter_cli", "some.library", "blizzard.cli")
    document = _export(_own_trace(), scope=scopes[0])
    document["resourceSpans"][0]["scopeSpans"] += [  # type: ignore[index]
        _export(_own_trace(), scope=scope)["resourceSpans"][0]["scopeSpans"][0]  # type: ignore[index]
        for scope in scopes[1:]
    ]
    with TestClient(app) as client:
        assert "partialSuccess" not in _post_json(client, document).json()
    names = {
        (span.instrumentation_scope.name if span.instrumentation_scope else ""): span.resource.attributes[
            "service.name"
        ]
        for span in _finished(handle, exporter)
    }
    assert names == {
        "winter_cli": "winter-blizzard",
        "some.library": "blizzard-worker-program",
        "blizzard.cli": "blizzard-cli",
    }


def test_winters_command_span_lands_in_the_step_and_another_chunks_is_dropped(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    handle = _handle(exporter)
    with TestClient(_app(tmp_path, handle, [], worker_programs=True)) as client:
        ours = _post_json(client, _export(_own_trace(), scope="winter_cli"))
        assert "partialSuccess" not in ours.json()
        foreign = trace_id(StepKey.attempt("ch_other", 1))
        theirs = _post_json(client, _export(foreign, scope="winter_cli"))
        assert int(theirs.json()["partialSuccess"]["rejectedSpans"]) == 1
    (span,) = _finished(handle, exporter)
    assert dict(span.attributes or {})["blizzard.lease.id"] == "lease_1"


def test_a_body_past_the_size_cap_is_refused_413(tmp_path: Path) -> None:
    handle = _handle(InMemorySpanExporter())
    with TestClient(_app(tmp_path, handle, [])) as client:
        declared = client.post(
            "/v1/traces", content=b"x" * (MAX_BODY_BYTES + 1), headers={"Content-Type": "application/json", **_TOKEN}
        )
        assert declared.status_code == 413
        streamed = client.post(
            "/v1/traces",
            content=iter([b"x" * MAX_BODY_BYTES, b"x"]),
            headers={"Content-Type": "application/json", **_TOKEN},
        )
        assert streamed.status_code == 413


def test_a_request_past_the_span_rate_is_refused_429_and_counted(tmp_path: Path) -> None:
    handle = _handle(InMemorySpanExporter())
    counter = ReceiverCounter()
    limiter = SpanRateLimiter(FixedClock(_NOW), capacity=1, refill_per_second=0.001)
    document = _export(_own_trace())
    document["resourceSpans"][0]["scopeSpans"][0]["spans"] *= 2  # type: ignore[index]
    with TestClient(_app(tmp_path, handle, [], counter, limiter)) as client:
        assert _post_json(client, document).status_code == 429
        assert counter.count().dropped == 2


@pytest.mark.parametrize(
    ("headers", "body", "expected"),
    [
        ({"Content-Type": "text/plain"}, b"{}", 415),
        ({"Content-Type": "application/json", "Content-Encoding": "gzip"}, b"{}", 415),
        ({"Content-Type": "application/json"}, b"not json", 400),
        ({"Content-Type": "application/x-protobuf"}, b"\xff\xff", 400),
    ],
)
def test_an_unsupported_or_malformed_request_is_refused(
    tmp_path: Path, headers: dict[str, str], body: bytes, expected: int
) -> None:
    handle = _handle(InMemorySpanExporter())
    with TestClient(_app(tmp_path, handle, [])) as client:
        assert client.post("/v1/traces", content=body, headers={**headers, **_TOKEN}).status_code == expected


def test_the_receiver_is_404_while_platform_tracing_is_off(tmp_path: Path) -> None:
    disabled = build_platform_tracing(TracingConfig(), {}, resource=_RESOURCE_OFF, scope="s", scope_version="1")
    with TestClient(_app(tmp_path, disabled, [])) as client:
        assert _post_json(client, _export(_own_trace())).status_code == 404


_RESOURCE_OFF = {"service.name": "blizzard-runner"}


def test_the_receiver_makes_no_server_span_of_its_own(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    handle = _handle(exporter)
    with TestClient(_app(tmp_path, handle, [])) as client:
        _post_json(client, _export(_own_trace()))
    spans = _finished(handle, exporter)
    assert [s for s in spans if s not in _worker_spans(spans)] == []


def test_a_non_404_lease_failure_propagates_and_an_unknown_hash_is_403(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from fastapi import HTTPException

    from blizzard.runner.api.wiring import RunnerWiring

    handle = _handle(InMemorySpanExporter())
    with TestClient(_app(tmp_path, handle, []), raise_server_exceptions=False) as client:
        assert _post_json(client, _export(_own_trace()), {"X-Blizzard-Lease-Token": "unknown"}).status_code == 403

        def failing(self, lease_id):  # type: ignore[no-untyped-def]
            raise HTTPException(status_code=503, detail="down")

        monkeypatch.setattr(RunnerWiring, "worker_lease", failing)
        assert _post_json(client, _export(_own_trace())).status_code == 503


def _ticked(
    tmp_path: Path,
    hub: FakeHub,
    seed: Callable[[LoopContext], None],
    run: Callable[[LoopContext], object] = tick,
) -> list[ReadableSpan]:
    exporter = InMemorySpanExporter()
    handle = _handle(exporter)
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    handle.instrument_client(client)
    ctx = make_context(
        store,
        hub=cast(FakeHub, _CallingHub(hub, client)),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(
            handle=WorkerHandle(session_id="sess-b", pid=200, process_start_time="start-200", pgid=200),
            verdict="pass",
        ),
        probe=FakeProbe(alive=set()),
    )
    ctx = type(ctx)(**{**ctx.__dict__, "tracer": handle.tracer})
    seed(ctx)
    run(ctx)
    return _finished(handle, exporter)


def _calls(spans: list[ReadableSpan], method: str) -> list[ReadableSpan]:
    calls = [s for s in spans if s.kind.name == "CLIENT" and str(_attr(s, "url.full")).endswith(f"/{method}")]
    return sorted(calls, key=lambda s: s.start_time or 0)


def _call(spans: list[ReadableSpan], method: str) -> ReadableSpan:
    (span,) = _calls(spans, method)
    return span


def _assert_directly_under(span: ReadableSpan, root: DerivedContext) -> None:
    assert span.context is not None and span.context.trace_id == root.trace_id
    assert _parent_id(span) == root.span_id


def _lease(epoch: int) -> NewLease:
    return NewLease(
        lease_id=f"lease_{epoch}",
        chunk_id="ch_1",
        graph_id="gr_1",
        node_id="nd_build",
        node_name="build",
        epoch=epoch,
        runner_id=_RUNNER,
        retries_max=2,
        created_at=_NOW,
    )


def _buffer_closed(ctx: LoopContext, epoch: int, enqueue: Callable[[OutboundFacts, LeaseRecord], None]) -> None:
    ctx.stores.lease_record.record_lease(_lease(epoch))
    lease = ctx.stores.lease_record.active_lease(f"lease_{epoch}")
    assert lease is not None
    enqueue(OutboundFacts(ctx), lease)
    ctx.stores.lease_record.record_closure(
        lease_id=lease.lease_id, chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW
    )


def test_a_buffered_completion_posts_under_its_attempt_step_root(tmp_path: Path) -> None:
    hub = FakeHub()
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.DONE)]
    submission = CompletionSubmission(choice="pass", epoch=3, runner_id=_RUNNER, from_node_id="nd_build")

    def seed(ctx: LoopContext) -> None:
        _buffer_closed(ctx, 3, lambda facts, lease: facts.completion(lease, submission, at=_NOW))

    spans = _ticked(tmp_path, hub, seed)
    _assert_directly_under(_call(spans, "submit_completion"), step_root(StepKey.attempt("ch_1", 3)))


def test_a_buffered_decision_posts_under_its_attempt_step_root(tmp_path: Path) -> None:
    submission = DecisionSubmission(from_node_id="nd_build", epoch=4, runner_id=_RUNNER)

    def seed(ctx: LoopContext) -> None:
        _buffer_closed(ctx, 4, lambda facts, lease: facts.decision(lease, submission, at=_NOW))

    spans = _ticked(tmp_path, FakeHub(), seed)
    _assert_directly_under(_call(spans, "submit_decision"), step_root(StepKey.attempt("ch_1", 4)))


def _held(ctx: LoopContext) -> None:
    ctx.stores.environments.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


def test_a_resolved_gate_applies_under_its_gate_step_root(tmp_path: Path) -> None:
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkStatusView(
        chunk_id="ch_1",
        status=ChunkStatus.RUNNING,
        latest_epoch=2,
        route_runner_id=_RUNNER,
        decision=ChunkDecisionStatusView(
            decision_id="dec_1", node_id="nd_gate", epoch=2, resolved_choice="approve", transitioned=False
        ),
    )
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.PARKED_AT_GATE)]
    spans = _ticked(tmp_path, hub, _held)
    _assert_directly_under(_call(spans, "submit_completion"), step_root(StepKey.gate("ch_1", 2, "dec_1")))


def _advanced_to(epoch: int) -> FakeHub:
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkStatusView(
        chunk_id="ch_1", status=ChunkStatus.RUNNING, latest_epoch=epoch, route_runner_id=_RUNNER
    )
    hub.envelopes["ch_1"] = make_envelope("ch_1", "verify", node_id="nd_verify", choices=[("pass", "done")])
    return hub


def _held_after_lease_1(ctx: LoopContext) -> None:
    ctx.stores.lease_record.record_lease(_lease(1))
    ctx.stores.lease_record.record_closure(
        lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW
    )
    _held(ctx)


def test_an_adopted_chunk_reads_its_envelope_under_the_views_latest_step(tmp_path: Path) -> None:
    spans = _ticked(tmp_path, _advanced_to(5), _held_after_lease_1)
    _assert_directly_under(_calls(spans, "get_envelope")[0], step_root(StepKey.attempt("ch_1", 5)))


def test_an_advanced_held_chunk_reads_its_envelope_under_the_views_latest_step(tmp_path: Path) -> None:
    spans = _ticked(tmp_path, _advanced_to(5), _held_after_lease_1, run=lambda ctx: Advance(ctx).run())
    _assert_directly_under(_call(spans, "get_envelope"), step_root(StepKey.attempt("ch_1", 5)))


@pytest.mark.parametrize("latest_epoch", [1, 4])
def test_a_hub_node_poll_parents_one_past_the_views_latest_epoch_even_ahead_of_the_lease_record(
    tmp_path: Path, latest_epoch: int
) -> None:
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkStatusView(
        chunk_id="ch_1", status=ChunkStatus.DELIVERING, latest_epoch=latest_epoch, route_runner_id=_RUNNER
    )
    spans = _ticked(tmp_path, hub, _held_after_lease_1)
    _assert_directly_under(_call(spans, "hub_advance"), step_root(StepKey.attempt("ch_1", latest_epoch + 1)))
