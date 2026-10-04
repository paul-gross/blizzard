"""The runner's platform spans (component tier) — the served app and the tick over an in-memory exporter."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient
from google.protobuf.json_format import MessageToDict, Parse
from google.protobuf.message import Message
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
    ExportMetricsServiceResponse,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue
from opentelemetry.sdk._logs import ReadableLogRecord
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, LogRecordExporter, LogRecordExportResult
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    Metric,
    MetricExporter,
    MetricExportResult,
    MetricsData,
    NumberDataPoint,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.harness_telemetry_outcome import HarnessTelemetryOutcome
from blizzard.foundation.platform_tracing.handle import IPlatformTracing, build_platform_tracing
from blizzard.foundation.platform_tracing.received_export import (
    IReceivedTelemetryExport,
    build_received_telemetry_export,
)
from blizzard.foundation.tokens import TokenHash
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.foundation.trace_ids import DerivedContext, StepKey, step_root, trace_id
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.leases import Lease, NewLease
from blizzard.runner.domain.tracing.attributes import RUNNER_ID
from blizzard.runner.domain.tracing.platform import (
    PLATFORM_INSTRUMENTATION_SCOPE,
    PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
    TICK_STEP,
)
from blizzard.runner.domain.tracing.receiver import MAX_BODY_BYTES
from blizzard.runner.domain.tracing.receiver_limits import (
    ReceiverBounds,
    ReceiverCount,
    ReceiverCounter,
    SpanRateLimiter,
)
from blizzard.runner.domain.tracing.status import LeaseTraceStatusReader
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.harness_telemetry import plan_harness_telemetry
from blizzard.runner.harness.internal.claude_code_section import ClaudeCodeSection
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.outbound import OutboundFacts
from blizzard.runner.loop.steps import Advance
from blizzard.runner.loop.tick import tick
from blizzard.wire.chunk import ChunkDecisionStatusView, ChunkStatusView
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse
from tests import claude_code_telemetry
from tests.harness_sections import sections
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
    harness_telemetry: bool = False,
    received: IReceivedTelemetryExport | None = None,
    metric_bounds: ReceiverBounds | None = None,
    log_bounds: ReceiverBounds | None = None,
    claude_counter: ReceiverCounter | None = None,
):
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(
        root=tmp_path,
        db_url=f"sqlite:///{tmp_path / 'runner.db'}",
        hub_url="http://hub.local:8421",
        tracing=TracingConfig(
            worker_programs=worker_programs,
            worker_program_services=services or {},
            harness_telemetry=harness_telemetry,
        ),
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
        claude_trace_counter=claude_counter,
        metric_bounds=metric_bounds,
        log_bounds=log_bounds,
        received_telemetry=received,
        trace_status=LeaseTraceStatusReader(
            settings=TracingSettings.of(_ENDPOINT),
            leases=make_stores(store).lease_traces,
            clock=FixedClock(_NOW),
            receiver=counter,
            claude_trace_receiver=claude_counter,
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


def test_worker_programs_caps_a_program_span_but_a_cli_span_keeps_its_declared_attributes_late_in_a_wide_span(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from blizzard.runner.api import otlp_receiver

    admitted: list[list[str]] = []
    real_admit = otlp_receiver.admit

    def spy(spans, lease, allowlist):  # type: ignore[no-untyped-def]
        admitted.append([span.scope_name for span in spans])
        return real_admit(spans, lease, allowlist)

    monkeypatch.setattr(otlp_receiver, "admit", spy)
    filler = [{"key": f"filler.{i:03d}", "value": {"stringValue": "x"}} for i in range(70)]
    declared = [
        {"key": "blizzard.cli.command", "value": {"stringValue": "artifact create"}},
        {"key": "process.exit.code", "value": {"intValue": "0"}},
    ]
    document = _export(_own_trace(), scope="blizzard.cli", extra={"attributes": filler + declared})
    document["resourceSpans"][0]["scopeSpans"].append(  # type: ignore[index]
        _export(_own_trace(), scope="some.library", extra={"attributes": filler + declared})["resourceSpans"][0][  # type: ignore[index]
            "scopeSpans"
        ][0]
    )
    exporter = InMemorySpanExporter()
    handle = _handle(exporter)
    with TestClient(_app(tmp_path, handle, [], worker_programs=True)) as client:
        assert "partialSuccess" not in _post_json(client, document).json()
    by_scope = {
        (span.instrumentation_scope.name if span.instrumentation_scope else ""): dict(span.attributes or {})
        for span in _finished(handle, exporter)
    }
    assert by_scope["blizzard.cli"] == {
        "blizzard.cli.command": "artifact create",
        "process.exit.code": 0,
        _CALLER: "worker",
        "blizzard.chunk.id": "ch_1",
        "blizzard.lease.id": "lease_1",
    }
    assert len(by_scope["some.library"]) == 64 + 3
    assert "blizzard.cli.command" not in by_scope["some.library"]
    assert sorted(batch for batch in admitted if batch) == [["blizzard.cli"], ["some.library"]]


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


def _buffer_closed(ctx: LoopContext, epoch: int, enqueue: Callable[[OutboundFacts, Lease], None]) -> None:
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


# --- harness telemetry: Claude Code's own metrics, logs and traces ---

_CLAUDE_METRICS_SCOPE = "com.anthropic.claude_code"
_CLAUDE_LOGS_SCOPE = "com.anthropic.claude_code.events"
_CLAUDE_TRACING_SCOPE = "com.anthropic.claude_code.tracing"
_CLAUDE_SERVICE = "blizzard-claude-code"
_ENCODED = {"json": "application/json", "protobuf": "application/x-protobuf"}
_NEW_PATHS = ["/v1/metrics", "/v1/logs"]
_STAMPS = {_CALLER: "worker", "blizzard.chunk.id": "ch_1", "blizzard.lease.id": "lease_1", RUNNER_ID: _RUNNER}


class _CapturingMetricExporter(MetricExporter):
    def __init__(self) -> None:
        super().__init__()
        self.exported: list[MetricsData] = []

    def export(self, metrics_data: MetricsData, timeout_millis: float = 10_000, **kwargs: object) -> MetricExportResult:
        self.exported.append(metrics_data)
        return MetricExportResult.SUCCESS

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return True

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: object) -> None:
        return None

    def points(self) -> list[tuple[Resource, str, Metric, NumberDataPoint]]:
        return [
            (resource_metrics.resource, scope_metrics.scope.name, metric, point)
            for data in self.exported
            for resource_metrics in data.resource_metrics
            for scope_metrics in resource_metrics.scope_metrics
            for metric in scope_metrics.metrics
            for point in metric.data.data_points  # type: ignore[union-attr]
        ]


class _Telemetry:
    """The runner's received-telemetry export over in-memory exporters, with what each one saw."""

    def __init__(self, *, harness_telemetry: bool = True) -> None:
        self.metrics = _CapturingMetricExporter()
        self.logs = InMemoryLogRecordExporter()
        self.handle = build_received_telemetry_export(
            TracingConfig(platform=True, harness_telemetry=harness_telemetry),
            _ENDPOINT,
            resource={"service.name": "blizzard-runner"},
            metric_exporter=self.metrics,
            log_exporter=self.logs,
        )

    def metric_points(self) -> list[tuple[Resource, str, Metric, NumberDataPoint]]:
        """What the metric exporter saw once the handle's queue has drained."""
        self.handle.shutdown(5.0)
        return self.metrics.points()

    def log_records(self) -> tuple[ReadableLogRecord, ...]:
        """What the log exporter saw once the handle's batch processor has drained."""
        self.handle.shutdown(5.0)
        return self.logs.get_finished_logs()


def _metrics_body(
    *, scope: str | None = None, repeat: int = 1, extra: dict[str, str] | None = None, encoding: str = "protobuf"
) -> bytes:
    message = ExportMetricsServiceRequest()
    message.ParseFromString(claude_code_telemetry.protobuf_body("metrics"))
    for resource in message.resource_metrics:
        for scope_metrics in resource.scope_metrics:
            if scope is not None:
                scope_metrics.scope.name = scope
            for metric in scope_metrics.metrics:
                points = metric.sum.data_points
                originals = list(points)
                for _ in range(repeat - 1):
                    points.extend(originals)
                for point in points:
                    for key, value in (extra or {}).items():
                        point.attributes.add(key=key, value=AnyValue(string_value=value))
    return _encoded_body(message, encoding)


def _logs_body(
    *,
    scope: str | None = None,
    repeat: int = 1,
    trace: int | None = None,
    extra: dict[str, str] | None = None,
    encoding: str = "protobuf",
) -> bytes:
    message = ExportLogsServiceRequest()
    message.ParseFromString(claude_code_telemetry.protobuf_body("logs"))
    for resource in message.resource_logs:
        for scope_logs in resource.scope_logs:
            if scope is not None:
                scope_logs.scope.name = scope
            originals = list(scope_logs.log_records)
            for _ in range(repeat - 1):
                scope_logs.log_records.extend(originals)
            for record in scope_logs.log_records:
                if trace is not None:
                    record.trace_id = trace.to_bytes(16, "big")
                for key, value in (extra or {}).items():
                    record.attributes.add(key=key, value=AnyValue(string_value=value))
    return _encoded_body(message, encoding)


def _claude_spans_body(*, scope: str | None = None, repeat: int = 1, encoding: str = "protobuf") -> bytes:
    message = ExportTraceServiceRequest()
    message.ParseFromString(claude_code_telemetry.protobuf_body("traces"))
    for resource in message.resource_spans:
        for scope_spans in resource.scope_spans:
            if scope is not None:
                scope_spans.scope.name = scope
            originals = list(scope_spans.spans)
            for _ in range(repeat - 1):
                scope_spans.spans.extend(originals)
            for span in scope_spans.spans:
                span.trace_id = _own_trace().to_bytes(16, "big")
    return _encoded_body(message, encoding)


def _encoded_body(message: Message, encoding: str) -> bytes:
    """The message as the recorded export carries it (protobuf), or as OTLP/JSON with hex ids."""
    if encoding == "protobuf":
        return message.SerializeToString()
    return json.dumps(claude_code_telemetry.hex_ids(MessageToDict(message))).encode()


def _post(client: TestClient, path: str, body: bytes, encoding: str = "protobuf", token: bool = True):  # type: ignore[no-untyped-def]
    return client.post(path, content=body, headers={"Content-Type": _ENCODED[encoding], **(_TOKEN if token else {})})


def _body_for(path: str, **kwargs: object) -> bytes:
    return _metrics_body(**kwargs) if path == "/v1/metrics" else _logs_body(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("encoding", ["json", "protobuf"])
def test_claude_code_metrics_export_stamped_under_its_service_name(tmp_path: Path, encoding: str) -> None:
    telemetry = _Telemetry()
    handle = _handle(InMemorySpanExporter())
    app = _app(tmp_path, handle, [], harness_telemetry=True, received=telemetry.handle)
    with TestClient(app) as client:
        response = _post(client, "/v1/metrics", _metrics_body(encoding=encoding), encoding)
        assert response.status_code == 200
        assert "partialSuccess" not in response.json() if encoding == "json" else response.content == b""
    exported = telemetry.metric_points()
    assert [metric.name for _, _, metric, _ in exported] == [
        "claude_code.session.count",
        "claude_code.active_time.total",
    ]
    for resource, scope, metric, point in exported:
        assert resource.attributes["service.name"] == _CLAUDE_SERVICE
        assert scope == _CLAUDE_METRICS_SCOPE
        assert {key: (point.attributes or {})[key] for key in _STAMPS} == _STAMPS
        assert metric.data.aggregation_temporality == AggregationTemporality.DELTA  # type: ignore[union-attr]
    first = exported[0][3]
    assert (first.start_time_unix_nano, first.time_unix_nano, first.value) == (
        1791055936556000000,
        1791055936709000000,
        1.0,
    )


@pytest.mark.parametrize("encoding", ["json", "protobuf"])
def test_claude_code_logs_export_stamped_under_its_service_name(tmp_path: Path, encoding: str) -> None:
    telemetry = _Telemetry()
    handle = _handle(InMemorySpanExporter())
    app = _app(tmp_path, handle, [], harness_telemetry=True, received=telemetry.handle)
    with TestClient(app) as client:
        assert _post(client, "/v1/logs", _logs_body(trace=_own_trace(), encoding=encoding), encoding).status_code == 200
    records = telemetry.log_records()
    assert len(records) == 6
    for readable in records:
        assert readable.resource.attributes["service.name"] == _CLAUDE_SERVICE
        assert readable.instrumentation_scope is not None
        assert readable.instrumentation_scope.name == _CLAUDE_LOGS_SCOPE
        attributes = dict(readable.log_record.attributes or {})
        assert {key: attributes[key] for key in _STAMPS} == _STAMPS
        assert readable.log_record.trace_id == _own_trace()


def test_a_log_record_naming_another_trace_loses_its_trace_context(tmp_path: Path) -> None:
    telemetry = _Telemetry()
    handle = _handle(InMemorySpanExporter())
    app = _app(tmp_path, handle, [], harness_telemetry=True, received=telemetry.handle)
    with TestClient(app) as client:
        assert _post(client, "/v1/logs", _logs_body()).status_code == 200
    records = telemetry.log_records()
    assert len(records) == 6
    assert {readable.log_record.trace_id for readable in records} <= {0, None}


def test_claude_codes_identity_attributes_reach_the_exporter_unchanged(tmp_path: Path) -> None:
    telemetry = _Telemetry()
    handle = _handle(InMemorySpanExporter())
    app = _app(tmp_path, handle, [], harness_telemetry=True, received=telemetry.handle)
    identity = {
        "user.email": "dev@example.test",
        "user.account_uuid": "acct-1",
        "user.id": "user-1",
        "organization.id": "org-1",
    }
    with TestClient(app) as client:
        assert _post(client, "/v1/metrics", _metrics_body(extra=identity)).status_code == 200
        assert _post(client, "/v1/logs", _logs_body(extra=identity)).status_code == 200
    points = telemetry.metric_points()
    records = telemetry.log_records()
    assert (len(points), len(records)) == (2, 6)
    for _, _, _, point in points:
        assert {key: (point.attributes or {})[key] for key in identity} == identity
    for readable in records:
        assert {key: (readable.log_record.attributes or {})[key] for key in identity} == identity


@pytest.mark.parametrize("path", _NEW_PATHS)
@pytest.mark.parametrize("headers", [{}, {"X-Blizzard-Lease-Token": "wrong"}])
def test_the_new_receivers_refuse_a_missing_or_unknown_token_403(
    tmp_path: Path, path: str, headers: dict[str, str]
) -> None:
    telemetry = _Telemetry()
    app = _app(tmp_path, _handle(InMemorySpanExporter()), [], harness_telemetry=True, received=telemetry.handle)
    with TestClient(app) as client:
        response = client.post(
            path, content=_body_for(path), headers={"Content-Type": "application/x-protobuf", **headers}
        )
        assert response.status_code == 403
    assert telemetry.metric_points() == [] and not telemetry.log_records()


@pytest.mark.parametrize("path", _NEW_PATHS)
def test_the_new_receivers_refuse_a_closed_leases_token_403(tmp_path: Path, path: str) -> None:
    telemetry = _Telemetry()
    app = _app(tmp_path, _handle(InMemorySpanExporter()), [], harness_telemetry=True, received=telemetry.handle)
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="done", closed_at=_NOW)
    with TestClient(app) as client:
        assert _post(client, path, _body_for(path)).status_code == 403


@pytest.mark.parametrize("path", _NEW_PATHS)
def test_the_new_receivers_are_404_while_either_switch_is_off(tmp_path: Path, path: str) -> None:
    off = build_platform_tracing(TracingConfig(), {}, resource=_RESOURCE_OFF, scope="s", scope_version="1")
    with TestClient(_app(tmp_path, off, [], harness_telemetry=True)) as client:
        assert _post(client, path, _body_for(path)).status_code == 404
    (tmp_path / "second").mkdir()
    with TestClient(_app(tmp_path / "second", _handle(InMemorySpanExporter()), [])) as client:
        assert _post(client, path, _body_for(path)).status_code == 404


@pytest.mark.parametrize("path", _NEW_PATHS)
def test_the_new_receivers_refuse_a_body_past_the_size_cap_413(tmp_path: Path, path: str) -> None:
    app = _app(tmp_path, _handle(InMemorySpanExporter()), [], harness_telemetry=True)
    with TestClient(app) as client:
        headers = {"Content-Type": "application/json", **_TOKEN}
        assert client.post(path, content=b"x" * (MAX_BODY_BYTES + 1), headers=headers).status_code == 413
        streamed = client.post(path, content=iter([b"x" * MAX_BODY_BYTES, b"x"]), headers=headers)
        assert streamed.status_code == 413


@pytest.mark.parametrize("path", _NEW_PATHS)
@pytest.mark.parametrize(
    ("headers", "body", "expected"),
    [
        ({"Content-Type": "text/plain"}, b"{}", 415),
        ({"Content-Type": "application/json", "Content-Encoding": "gzip"}, b"{}", 415),
        ({"Content-Type": "application/json"}, b"not json", 400),
        ({"Content-Type": "application/x-protobuf"}, b"\xff\xff", 400),
    ],
)
def test_the_new_receivers_refuse_an_unsupported_or_malformed_request(
    tmp_path: Path, path: str, headers: dict[str, str], body: bytes, expected: int
) -> None:
    app = _app(tmp_path, _handle(InMemorySpanExporter()), [], harness_telemetry=True)
    with TestClient(app) as client:
        assert client.post(path, content=body, headers={**headers, **_TOKEN}).status_code == expected


@pytest.mark.parametrize("path", _NEW_PATHS)
def test_the_new_receivers_refuse_a_request_past_the_rate_429_and_count_it(tmp_path: Path, path: str) -> None:
    bounds = ReceiverBounds(SpanRateLimiter(FixedClock(_NOW), capacity=1, refill_per_second=0.001), ReceiverCounter())
    app = _app(
        tmp_path,
        _handle(InMemorySpanExporter()),
        [],
        harness_telemetry=True,
        metric_bounds=bounds,
        log_bounds=bounds,
    )
    with TestClient(app) as client:
        refused = _post(client, path, _body_for(path, repeat=2))
        assert (refused.status_code, refused.headers.get("Retry-After")) == (429, "1")
    assert bounds.counter.count().accepted == 0
    assert bounds.counter.count().dropped > 0


def test_each_signal_is_limited_and_counted_on_its_own(tmp_path: Path) -> None:
    telemetry = _Telemetry()
    metric_bounds = ReceiverBounds.fresh(FixedClock(_NOW))
    log_bounds = ReceiverBounds.fresh(FixedClock(_NOW))
    counter = ReceiverCounter()
    app = _app(
        tmp_path,
        _handle(InMemorySpanExporter()),
        [],
        counter,
        harness_telemetry=True,
        received=telemetry.handle,
        metric_bounds=metric_bounds,
        log_bounds=log_bounds,
    )
    with TestClient(app) as client:
        assert _post(client, "/v1/metrics", _metrics_body()).status_code == 200
        assert _post(client, "/v1/logs", _logs_body(scope="some.library")).status_code == 200
    assert (metric_bounds.counter.count().accepted, metric_bounds.counter.count().dropped) == (2, 0)
    assert (log_bounds.counter.count().accepted, log_bounds.counter.count().dropped) == (0, 6)
    assert counter.count().accepted == 0


@pytest.mark.parametrize("path", _NEW_PATHS)
def test_a_foreign_scope_flood_does_not_spend_the_kept_scopes_budget(tmp_path: Path, path: str) -> None:
    bounds = ReceiverBounds(SpanRateLimiter(FixedClock(_NOW), capacity=6, refill_per_second=0.001), ReceiverCounter())
    app = _app(
        tmp_path,
        _handle(InMemorySpanExporter()),
        [],
        harness_telemetry=True,
        metric_bounds=bounds,
        log_bounds=bounds,
    )
    with TestClient(app) as client:
        flood = _post(client, path, _body_for(path, scope="some.library", repeat=50))
        assert flood.status_code == 200
        assert _post(client, path, _body_for(path)).status_code == 200


def test_a_foreign_scope_flood_does_not_spend_the_trace_receivers_budget(tmp_path: Path) -> None:
    handle = _handle(InMemorySpanExporter())
    limiter = SpanRateLimiter(FixedClock(_NOW), capacity=2, refill_per_second=0.001)
    with TestClient(_app(tmp_path, handle, [], limiter=limiter, harness_telemetry=True)) as client:
        flood = _post(client, "/v1/traces", _claude_spans_body(scope="some.library", repeat=50))
        assert flood.status_code == 200
        assert _post(client, "/v1/traces", _claude_spans_body()).status_code == 200


def test_claude_code_spans_export_under_its_service_name_with_the_runner_id(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    handle = _handle(exporter)
    with TestClient(_app(tmp_path, handle, [], harness_telemetry=True)) as client:
        assert _post(client, "/v1/traces", _claude_spans_body()).status_code == 200
        assert _post(client, "/v1/traces", _claude_spans_body(encoding="json"), "json").status_code == 200
    spans = _finished(handle, exporter)
    assert len(spans) == 4
    for span in spans:
        assert span.resource.attributes["service.name"] == _CLAUDE_SERVICE
        assert {key: (span.attributes or {})[key] for key in _STAMPS} == _STAMPS
        assert (span.attributes or {})["user.id"] == "sanitized"


def test_claude_codes_spans_are_tallied_apart_from_the_worker_spans(tmp_path: Path) -> None:
    handle = _handle(InMemorySpanExporter())
    counter, claude_counter = ReceiverCounter(), ReceiverCounter()
    app = _app(tmp_path, handle, [], counter, claude_counter=claude_counter, harness_telemetry=True)
    with TestClient(app) as client:
        assert _post_json(client, _export(_own_trace())).status_code == 200
        assert _post(client, "/v1/traces", _claude_spans_body()).status_code == 200
    assert counter.count() == ReceiverCount(accepted=1, dropped=0)
    assert claude_counter.count() == ReceiverCount(accepted=2, dropped=0)


def test_claude_code_spans_are_dropped_while_harness_telemetry_is_off(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    handle = _handle(exporter)
    with TestClient(_app(tmp_path, handle, [])) as client:
        response = _post(client, "/v1/traces", _claude_spans_body())
        assert ExportTraceServiceResponse.FromString(response.content).partial_success.rejected_spans == 2
    assert _finished(handle, exporter) == []


def test_a_mapped_scope_renames_claude_codes_spans_and_worker_programs_no_longer_names_them(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    handle = _handle(exporter)
    app = _app(
        tmp_path,
        handle,
        [],
        worker_programs=True,
        services={_CLAUDE_TRACING_SCOPE: "claude-fleet"},
    )
    with TestClient(app) as client:
        assert _post(client, "/v1/traces", _claude_spans_body()).status_code == 200
    assert [span.resource.attributes["service.name"] for span in _finished(handle, exporter)] == ["claude-fleet"] * 2
    (tmp_path / "second").mkdir()
    unmapped_exporter = InMemorySpanExporter()
    unmapped = _handle(unmapped_exporter)
    unmapped_app = _app(tmp_path / "second", unmapped, [], worker_programs=True)
    with TestClient(unmapped_app) as client:
        assert _post(client, "/v1/traces", _claude_spans_body()).status_code == 200
    names = [span.resource.attributes["service.name"] for span in _finished(unmapped, unmapped_exporter)]
    assert names == [_CLAUDE_SERVICE] * 2


def test_claude_code_spans_are_kept_and_counted_while_traces_are_operator_configured(tmp_path: Path) -> None:
    settings = tmp_path / "worker-settings.json"
    settings.write_text(json.dumps({"env": {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://operator:4318"}}))
    config = RunnerConfig(
        root=tmp_path,
        db_url="sqlite://",
        harness_sections=sections(ClaudeCodeSection(worker_settings_path=str(settings))),
    )
    plan = plan_harness_telemetry(config, bundle=None, runner_environ=_ENDPOINT, enabled=True)
    assert plan.traces is HarnessTelemetryOutcome.OPERATOR_CONFIGURED

    exporter = InMemorySpanExporter()
    handle = _handle(exporter)
    claude_counter = ReceiverCounter()
    app = _app(tmp_path, handle, [], claude_counter=claude_counter, worker_programs=True, harness_telemetry=True)
    with TestClient(app) as client:
        assert _post(client, "/v1/traces", _claude_spans_body()).status_code == 200
    assert [span.resource.attributes["service.name"] for span in _finished(handle, exporter)] == [_CLAUDE_SERVICE] * 2
    assert claude_counter.count() == ReceiverCount(accepted=2, dropped=0)


def test_a_mapped_scope_renames_claude_codes_metrics(tmp_path: Path) -> None:
    telemetry = _Telemetry()
    app = _app(
        tmp_path,
        _handle(InMemorySpanExporter()),
        [],
        harness_telemetry=True,
        received=telemetry.handle,
        services={_CLAUDE_METRICS_SCOPE: "claude-fleet"},
    )
    with TestClient(app) as client:
        assert _post(client, "/v1/metrics", _metrics_body()).status_code == 200
    names = [resource.attributes["service.name"] for resource, _, _, _ in telemetry.metric_points()]
    assert names == ["claude-fleet"] * 2


@pytest.mark.parametrize("path", ["/v1/traces", *_NEW_PATHS])
def test_the_otlp_receivers_make_no_server_span(tmp_path: Path, path: str) -> None:
    exporter = InMemorySpanExporter()
    handle = _handle(exporter)
    telemetry = _Telemetry()
    with TestClient(_app(tmp_path, handle, [], harness_telemetry=True, received=telemetry.handle)) as client:
        _post(client, path, _body_for(path) if path != "/v1/traces" else _claude_spans_body())
    assert [s for s in _finished(handle, exporter) if (s.attributes or {}).get("http.route")] == []


class _HeldMetricExporter(_CapturingMetricExporter):
    """A destination that answers only once ``release`` is set, recording whether it was held past its patience."""

    def __init__(self, release: threading.Event) -> None:
        super().__init__()
        self.release = release
        self.timed_out = False

    def export(self, metrics_data: MetricsData, timeout_millis: float = 10_000, **kwargs: object) -> MetricExportResult:
        self.timed_out = self.timed_out or not self.release.wait(_HELD_SECONDS)
        return super().export(metrics_data)


class _HeldLogExporter(LogRecordExporter):
    def __init__(self, release: threading.Event) -> None:
        self.release = release
        self.timed_out = False
        self.exported: list[ReadableLogRecord] = []

    def export(self, batch: Sequence[ReadableLogRecord]) -> LogRecordExportResult:
        self.timed_out = self.timed_out or not self.release.wait(_HELD_SECONDS)
        self.exported.extend(batch)
        return LogRecordExportResult.SUCCESS

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


#: How long a held destination waits for release; a receiver that exported inline would hold its response this long.
_HELD_SECONDS = 5.0


def test_a_slow_destination_does_not_hold_the_receivers_response(tmp_path: Path) -> None:
    release = threading.Event()
    metrics = _HeldMetricExporter(release)
    logs = _HeldLogExporter(release)
    received = build_received_telemetry_export(
        TracingConfig(platform=True, harness_telemetry=True),
        _ENDPOINT,
        resource={"service.name": "blizzard-runner"},
        metric_exporter=metrics,
        log_exporter=logs,
    )
    app = _app(tmp_path, _handle(InMemorySpanExporter()), [], harness_telemetry=True, received=received)
    with TestClient(app) as client:
        for _ in range(2):
            assert _post(client, "/v1/metrics", _metrics_body()).status_code == 200
            assert _post(client, "/v1/logs", _logs_body()).status_code == 200
    release.set()
    received.shutdown(_HELD_SECONDS)
    assert not metrics.timed_out and not logs.timed_out
    assert len(metrics.points()) == 4
    assert len(logs.exported) == 12


def test_a_summary_point_is_refused_and_counted_not_lost(tmp_path: Path) -> None:
    telemetry = _Telemetry()
    bounds = ReceiverBounds.fresh(FixedClock(_NOW))
    message = ExportMetricsServiceRequest()
    message.ParseFromString(_metrics_body())
    scope_metrics = message.resource_metrics[0].scope_metrics[0]
    summary = scope_metrics.metrics.add(name="claude_code.latency").summary
    summary.data_points.add(count=1)
    summary.data_points.add(count=2)
    app = _app(
        tmp_path,
        _handle(InMemorySpanExporter()),
        [],
        harness_telemetry=True,
        received=telemetry.handle,
        metric_bounds=bounds,
    )
    with TestClient(app) as client:
        response = _post(client, "/v1/metrics", message.SerializeToString())
        assert response.status_code == 200
    assert ExportMetricsServiceResponse.FromString(response.content).partial_success.rejected_data_points == 2
    assert (bounds.counter.count().accepted, bounds.counter.count().dropped) == (2, 2)
    assert len(telemetry.metric_points()) == 2
