"""The hub's platform spans (component tier) — a real ``build_hosted_app`` over an in-memory exporter."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx2
import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from blizzard import __version__
from blizzard.auth_core import Role
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.operator_sessions.internal.session_file import SessionFile
from blizzard.foundation.platform_tracing.attributes import TENANT, TENANT_NAME
from blizzard.foundation.platform_tracing.handle import IPlatformTracing, build_platform_tracing
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.hub import app as hub_app
from blizzard.hub.cli import hub as hub_group
from blizzard.hub.config import AUTH_MODE_OAUTH, AuthConfig, HubConfig
from blizzard.hub.domain.observability.tracing.attributes import (
    PLATFORM_INSTRUMENTATION_SCOPE,
    PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
    resource_attributes,
)
from tests.config_inputs import build_hosted_hub_app, init_hub
from tests.support import RunnerFleetClient, hub_scoped, hub_scoped_engine, seed_runner, seed_user, tenant_services_of
from tests.worker_http import bind_transport

pytestmark = pytest.mark.component

_ENDPOINT = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"}
_CALLER = "blizzard.caller"
_RUNNER_ID = "blizzard.runner.id"
_RUNNER_NAME = "blizzard.runner.name"
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
    return init_hub(tmp_path / "hub")


def _spans(handle: IPlatformTracing, exporter: InMemorySpanExporter) -> list:
    handle.shutdown(5.0)
    return list(exporter.get_finished_spans())


def test_a_request_yields_a_route_template_server_span_with_parameterized_queries(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    config = _config(tmp_path)
    handle = _handle(config, exporter)
    app = build_hosted_hub_app(config, platform_tracing=handle)
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
    app = build_hosted_hub_app(config, platform_tracing=handle)
    app.state.shutdown = asyncio.Event()
    sweeps = list(hub_app.Sweep.all(app))
    assert {sweep.name for sweep in sweeps} >= {"transcript_events", "work_closure"}
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


def test_sweep_spans_carry_no_tenant_and_a_tenant_routes_request_span_carries_its_own(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    config = _config(tmp_path)
    handle = _handle(config, exporter)
    app = build_hosted_hub_app(config, platform_tracing=handle)
    app.state.shutdown = asyncio.Event()
    sweeps = list(hub_app.Sweep.all(app))
    with TestClient(app) as client:
        assert client.get("/api/chunks").status_code == 200

    async def one_pass_each() -> None:
        tasks = [asyncio.create_task(sweep.run()) for sweep in sweeps]
        await asyncio.sleep(0.5)
        app.state.shutdown.set()
        await asyncio.gather(*tasks)

    asyncio.run(one_pass_each())
    spans = _spans(handle, exporter)
    (server,) = [s for s in spans if s.name == "GET /api/chunks"]
    assert server.attributes is not None
    assert server.attributes[TENANT] == app.state.services.tenants.open_tenants.only()
    sweep_traces = {s.context.trace_id for s in spans if s.name.startswith("sweep ")}
    assert {s.name for s in spans if s.parent is None and s.context.trace_id in sweep_traces} == {
        f"sweep {sweep.name}" for sweep in sweeps
    }
    in_sweeps = [s for s in spans if s.context.trace_id in sweep_traces]
    assert not [s.name for s in in_sweeps if {TENANT, TENANT_NAME} & set(s.attributes or {})]


def test_a_runner_bearer_stamps_caller_runner_id_and_name_and_a_forged_caller_header_changes_nothing(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    token = _registered_runner_token(config, "rn_01JRUNNERA", name="runner-a")
    exporter = InMemorySpanExporter()
    handle = _handle(config, exporter)
    app = build_hosted_hub_app(config, platform_tracing=handle)
    with TestClient(app) as client:
        resp = client.get(
            "/api/fleet/queue/peek", headers={"Authorization": f"Bearer {token}", "blizzard.caller": "operator"}
        )
        assert resp.status_code == 200
    server = [s for s in _spans(handle, exporter) if s.name.startswith("GET /api/fleet/queue/peek")]
    assert len(server) == 1
    assert server[0].attributes[_CALLER] == "runner"
    assert (server[0].attributes[_RUNNER_ID], server[0].attributes[_RUNNER_NAME]) == ("rn_01JRUNNERA", "runner-a")
    assert "operator" not in {s.attributes.get(_CALLER) for s in server}


def test_an_outbound_call_through_an_injected_oauth_client_is_an_httpx_client_span(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    config = _config(tmp_path)
    handle = _handle(config, exporter)
    oauth = httpx2.Client(transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json={})))
    build_hosted_hub_app(config, platform_tracing=handle, oauth_http_client=oauth)
    oauth.get("https://idp.example/token?code=tok-secret-9")
    spans = _spans(handle, exporter)
    client_spans = [s for s in spans if s.kind.name == "CLIENT" and "idp.example" in repr(dict(s.attributes or {}))]
    assert client_spans
    assert "tok-secret-9" not in repr([dict(s.attributes or {}) for s in spans])


def test_platform_off_with_an_endpoint_set_builds_no_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    config = _config(tmp_path)
    assert config.tracing == TracingConfig()
    app = build_hosted_hub_app(config)
    with TestClient(app) as client:
        client.get("/api/chunks/ch_x")
    from opentelemetry import trace

    assert type(trace.get_tracer_provider()).__name__ == "ProxyTracerProvider"


def test_a_route_without_a_chunk_id_carries_no_chunk_attribute(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    config = _config(tmp_path)
    handle = _handle(config, exporter)
    app = build_hosted_hub_app(config, platform_tracing=handle)
    with TestClient(app) as client:
        client.get("/api/health")
    server = [s for s in _spans(handle, exporter) if s.name == "GET /api/health"]
    assert len(server) == 1
    assert _CHUNK_ID not in (server[0].attributes or {})


_PLANTED_TRACE = 0x4BF92F3577B34DA6A3CE929D0E0E4736
_PLANTED_SPAN = 0x00F067AA0BA902B7
_TRACEPARENT = {"traceparent": f"00-{_PLANTED_TRACE:032x}-{_PLANTED_SPAN:016x}-01"}


def _registered_runner_token(config: HubConfig, runner_id: str, *, name: str | None = None) -> str:
    """Add and register ``runner_id`` through an untraced app over the same store, so the seeding
    leaves no spans behind."""
    return seed_runner(tenant_services_of(build_hosted_hub_app(config)), runner_id, name=name, workspace_id="ws-a")


def _server_span(spans: list, prefix: str):
    server = [s for s in spans if s.kind.name == "SERVER" and s.name.startswith(prefix)]
    assert len(server) == 1, [s.name for s in spans]
    return server[0]


def _continues_planted(span) -> bool:
    return span.context.trace_id == _PLANTED_TRACE and span.parent is not None and span.parent.span_id == _PLANTED_SPAN


def test_a_registered_runner_bearer_continues_an_incoming_trace(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    config = _config(tmp_path)
    handle = _handle(config, exporter)
    with TestClient(build_hosted_hub_app(config, platform_tracing=handle)) as client:
        token = _registered_runner_token(config, "runner-a")
        client.get("/api/fleet/queue/peek", headers={"Authorization": f"Bearer {token}", **_TRACEPARENT})
    spans = _spans(handle, exporter)
    assert _continues_planted(_server_span(spans, "GET /api/fleet/queue/peek"))
    gate_lookups = [
        s
        for s in spans
        if s.parent is None and "runner_registrations" in str((s.attributes or {}).get("db.query.text"))
    ]
    assert not gate_lookups, "the gate's own lookups opened root spans"


def test_a_traced_runner_request_resolves_its_token_once(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    config = _config(tmp_path)
    handle = _handle(config, exporter)
    with TestClient(build_hosted_hub_app(config, platform_tracing=handle)) as client:
        token = _registered_runner_token(config, "runner-a")
        credentials = client.app.state.services.hub_scoped_reads.credentials  # type: ignore[attr-defined]
        resolutions: list[str] = []
        original = credentials.runner_for_token_hash

        def counting(token_hash: str):
            resolutions.append(token_hash)
            return original(token_hash)

        credentials.runner_for_token_hash = counting
        response = client.get("/api/fleet/queue/peek", headers={"Authorization": f"Bearer {token}", **_TRACEPARENT})
    assert response.status_code == 200
    assert len(resolutions) == 1


@pytest.mark.parametrize("credential", ["anonymous", "unknown", "revoked"])
def test_an_unresolved_runner_credential_starts_a_fresh_root(tmp_path: Path, credential: str) -> None:
    exporter = InMemorySpanExporter()
    config = _config(tmp_path)
    handle = _handle(config, exporter)
    with TestClient(build_hosted_hub_app(config, platform_tracing=handle)) as client:
        token = _registered_runner_token(config, "runner-a")
        if credential == "revoked":
            assert client.post("/api/runners/runner-a/token-revocations", json={}).status_code == 201
        headers = dict(_TRACEPARENT)
        if credential != "anonymous":
            headers["Authorization"] = f"Bearer {token if credential == 'revoked' else 'not-a-token'}"
        client.get("/api/fleet/queue/peek", headers=headers)
    server = _server_span(_spans(handle, exporter), "GET /api/fleet/queue/peek")
    assert server.parent is None
    assert server.context.trace_id != _PLANTED_TRACE


def test_a_human_route_under_auth_mode_none_starts_a_fresh_root(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    config = _config(tmp_path)
    assert config.auth.mode == "none"
    handle = _handle(config, exporter)
    with TestClient(build_hosted_hub_app(config, platform_tracing=handle)) as client:
        client.get("/api/chunks", headers=_TRACEPARENT)
    server = _server_span(_spans(handle, exporter), "GET /api/chunks")
    assert server.parent is None
    assert server.context.trace_id != _PLANTED_TRACE


_PASS_GRAPH_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    judgement:
      prompt: |
        Assess the build.
      choices:
        pass:
          description: Complete and green.
          to: merge
        fail:
          description: Incomplete.
          to: build
  merge:
    executor: hub
    action: pass
    judgement:
      choices:
        success:
          description: Landed.
          to: done
        failure:
          description: Failed.
          to: build
"""


def test_a_hub_action_pass_parents_on_its_turns_hub_exec_span_which_the_trace_corpus_derives_alike(
    tmp_path: Path,
) -> None:
    """The driver takes the ``merge`` visit as a turn under no request span, so no request links it; its pass parents
    on the turn's hub-exec context, which is the ``hub exec`` span the corpus assembles from the turn's row."""
    import time
    from dataclasses import replace

    from sqlalchemy import select

    from blizzard.foundation.trace_ids import DerivedContext, SpanRole, StepKey
    from blizzard.hub.domain.observability.tracing.assembly import assemble_step
    from blizzard.hub.domain.observability.tracing.platform import (
        ACTION_CHOICE,
        ACTION_NAME,
        ACTION_PASS_SPAN,
        RUN_STEP_SPAN,
    )
    from blizzard.hub.domain.observability.tracing.steps import StepKind, identify_steps
    from blizzard.hub.store import schema as s
    from blizzard.hub.store.internal.corpus_rows import step_facts_for

    exporter = InMemorySpanExporter()
    config = _config(tmp_path)
    handle = _handle(config, exporter)
    app = build_hosted_hub_app(config, platform_tracing=handle)
    with RunnerFleetClient(app, services=tenant_services_of(app)) as client:
        assert client.post("/api/graphs", json={"definition_yaml": _PASS_GRAPH_YAML}).status_code == 201
        chunk_id = client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()["chunk_id"]
        assert client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
        assert (
            client.post(
                "/api/fleet/runners",
                json={
                    "runner_id": "r1",
                    "workspace_id": "w1",
                    "capabilities": [{"harness_id": "claude", "default": True}],
                },
            ).status_code
            == 201
        )
        claim = client.post(
            "/api/fleet/routes",
            json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
        )
        assert claim.status_code == 201, claim.text
        build_node_id = claim.json()["envelope"]["node"]["node_id"]
        minted = {"seq": 1, "kind": "lease.minted", "payload": {"chunk_id": chunk_id, "epoch": 1}}
        assert client.post("/api/fleet/events", json={"runner_id": "r1", "facts": [minted]}).status_code == 200
        applied = client.post(
            f"/api/fleet/chunks/{chunk_id}/completions",
            json={
                "choice": "pass",
                "epoch": 1,
                "runner_id": "r1",
                "from_node_id": build_node_id,
                "check_results": [],
                "artifacts": [],
            },
        )
        assert applied.json()["outcome"] == "hub_node_taken", applied.text
        turn_query = hub_scoped(
            select(s.hub_turns.c.turn_id)
            .join(s.flow_submissions, s.flow_submissions.c.submission_id == s.hub_turns.c.submission_id)
            .where(s.flow_submissions.c.chunk_id == chunk_id, s.hub_turns.c.ended_at.is_not(None))
        )
        deadline = time.monotonic() + 10
        while True:
            with app.state.engine.connect() as conn:
                ended = conn.execute(turn_query).all()
            if ended or time.monotonic() > deadline:
                break
            time.sleep(0.05)
        with hub_scoped_engine(app.state.engine).connect() as conn:
            _, facts, _ = step_facts_for(conn, [chunk_id])[chunk_id]
        assert facts.pin_graph_id is not None
        pinned = tenant_services_of(app).graphs.get(facts.pin_graph_id)
        assert pinned is not None
        facts = replace(facts, graphs={facts.pin_graph_id: pinned})
    (turn,) = ended
    spans = _spans(handle, exporter)
    hub_exec = DerivedContext.of(StepKey.attempt(chunk_id, 2), SpanRole.HUB_EXEC, turn.turn_id)

    completion = _server_span(spans, "POST /api/fleet/chunks/{chunk_id}/completions")
    assert (hub_exec.trace_id, hub_exec.span_id) not in [
        (link.context.trace_id, link.context.span_id) for link in completion.links
    ]
    (action_pass,) = [span for span in spans if span.name == ACTION_PASS_SPAN]
    assert action_pass.parent is not None
    assert (action_pass.parent.trace_id, action_pass.parent.span_id) == (hub_exec.trace_id, hub_exec.span_id)
    assert dict(action_pass.attributes or {}) == {ACTION_NAME: "pass", ACTION_CHOICE: "success"}
    steps = identify_steps(facts)
    (merge,) = [step for step in steps if step.kind is StepKind.HUB]
    told = [span for span in assemble_step(facts, merge, steps) if span.name == "hub exec"]
    assert [(span.context.trace_id, span.context.span_id) for span in told] == [(hub_exec.trace_id, hub_exec.span_id)]
    # The retired step loop emits no step span.
    assert not [span for span in spans if span.name == RUN_STEP_SPAN]


def test_an_operator_command_span_parents_the_hubs_server_span_for_an_authenticated_operator(tmp_path: Path) -> None:
    """The CLI's root span and the hub's server span share one trace, the hub's the child — through
    ``CliContext`` over the app's own transport, with an operator session under a real auth mode."""
    exporter = InMemorySpanExporter()
    config = replace(_config(tmp_path), auth=AuthConfig(mode=AUTH_MODE_OAUTH))
    handle = _handle(config, exporter)
    app = build_hosted_hub_app(config, platform_tracing=handle)
    posted: list[httpx2.Request] = []
    with TestClient(app) as client:

        def answer(request: httpx2.Request) -> httpx2.Response:
            if request.url.host == "collector.local":
                posted.append(request)
                return httpx2.Response(200)
            relayed = client.request(
                request.method,
                request.url.raw_path.decode(),
                headers=dict(request.headers),
                content=request.content,
            )
            return httpx2.Response(
                relayed.status_code, headers={"content-type": relayed.headers["content-type"]}, content=relayed.content
            )

        bound = bind_transport(answer)
        harness = SimpleNamespace(engine=app.state.engine, clock=FixedClock(datetime(2026, 7, 13, tzinfo=UTC)))
        session = app.state.services.auth.mint_session(
            seed_user(harness, username="op", role=Role.CONTRIBUTOR, email="op@example.com")  # type: ignore[arg-type]
        )[0]
        SessionFile.of().save("http://testserver", session)
        result = bound.runner.invoke(
            hub_group,
            ["chunk", "list"],
            env={"BZ_HUB_URL": "http://testserver", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector.local:4318"},
        )
    assert result.exit_code == 0, result.output
    [post] = posted
    cli_span = json.loads(post.content)["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    server = _server_span(_spans(handle, exporter), "GET /api/chunks")
    assert f"{server.context.trace_id:032x}" == cli_span["traceId"]
    assert server.parent is not None and f"{server.parent.span_id:016x}" == cli_span["spanId"]


def test_a_traced_session_request_resolves_its_session_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exporter = InMemorySpanExporter()
    config = replace(_config(tmp_path), auth=AuthConfig(mode=AUTH_MODE_OAUTH))
    handle = _handle(config, exporter)
    app = build_hosted_hub_app(config, platform_tracing=handle)
    with TestClient(app) as client:
        services = app.state.services
        harness = SimpleNamespace(engine=app.state.engine, clock=FixedClock(datetime(2026, 7, 13, tzinfo=UTC)))
        user = seed_user(cast(Any, harness), username="op", role=Role.CONTRIBUTOR, email="op@example.com")
        session = services.auth.mint_session(user)[0]
        calls: list[str] = []
        lookup, touch = services.sessions.get_by_hash, services.auth.touch_session
        monkeypatch.setattr(services.sessions, "get_by_hash", lambda h: calls.append("get_by_hash") or lookup(h))
        monkeypatch.setattr(
            services.auth, "touch_session", lambda s, **kw: calls.append("touch_session") or touch(s, **kw)
        )
        response = client.get(
            "/api/chunks",
            headers={"Authorization": f"Bearer {session}", "traceparent": f"00-{'a' * 32}-{'b' * 16}-01"},
        )
    assert response.status_code == 200, response.text
    assert calls == ["get_by_hash", "touch_session"]
