"""The hub's platform spans (component tier) — a real ``build_hosted_app`` over an in-memory exporter."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from blizzard import __version__
from blizzard.auth_core import Role
from blizzard.cli.collaborators import CliCollaborators
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.operator_sessions.internal.session_file import SessionFile
from blizzard.foundation.platform_tracing.handle import IPlatformTracing, build_platform_tracing
from blizzard.foundation.span_clock import Clock
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.hub import app as hub_app
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.cli import hub as hub_group
from blizzard.hub.config import AUTH_MODE_OAUTH, AuthConfig, HubConfig
from blizzard.hub.domain.observability.tracing.attributes import (
    PLATFORM_INSTRUMENTATION_SCOPE,
    PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
    resource_attributes,
)
from tests.support import RunnerFleetClient, seed_runner, seed_user

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


def test_a_runner_bearer_stamps_caller_runner_id_and_name_and_a_forged_caller_header_changes_nothing(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    token = _registered_runner_token(config, "rn_01JRUNNERA", name="runner-a")
    exporter = InMemorySpanExporter()
    handle = _handle(config, exporter)
    app = hub_app.build_hosted_app(config, platform_tracing=handle)
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


_PLANTED_TRACE = 0x4BF92F3577B34DA6A3CE929D0E0E4736
_PLANTED_SPAN = 0x00F067AA0BA902B7
_TRACEPARENT = {"traceparent": f"00-{_PLANTED_TRACE:032x}-{_PLANTED_SPAN:016x}-01"}


def _registered_runner_token(config: HubConfig, runner_id: str, *, name: str | None = None) -> str:
    """Add and register ``runner_id`` through an untraced app over the same store, so the seeding
    leaves no spans behind."""
    return seed_runner(hub_app.build_hosted_app(config).state.services, runner_id, name=name, workspace_id="ws-a")


def _server_span(spans: list, prefix: str):  # type: ignore[no-untyped-def,type-arg]
    server = [s for s in spans if s.kind.name == "SERVER" and s.name.startswith(prefix)]
    assert len(server) == 1, [s.name for s in spans]
    return server[0]


def _continues_planted(span) -> bool:  # type: ignore[no-untyped-def]
    return span.context.trace_id == _PLANTED_TRACE and span.parent is not None and span.parent.span_id == _PLANTED_SPAN


def test_a_registered_runner_bearer_continues_an_incoming_trace(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    config = _config(tmp_path)
    handle = _handle(config, exporter)
    with TestClient(hub_app.build_hosted_app(config, platform_tracing=handle)) as client:
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
    with TestClient(hub_app.build_hosted_app(config, platform_tracing=handle)) as client:
        token = _registered_runner_token(config, "runner-a")
        registry = client.app.state.services.registry  # type: ignore[attr-defined]
        resolutions: list[str] = []
        original = registry.registration_for_token_hash

        def counting(token_hash: str):  # type: ignore[no-untyped-def]
            resolutions.append(token_hash)
            return original(token_hash)

        registry.registration_for_token_hash = counting
        response = client.get("/api/fleet/queue/peek", headers={"Authorization": f"Bearer {token}", **_TRACEPARENT})
    assert response.status_code == 200
    assert len(resolutions) == 1


@pytest.mark.parametrize("credential", ["anonymous", "unknown", "revoked"])
def test_an_unresolved_runner_credential_starts_a_fresh_root(tmp_path: Path, credential: str) -> None:
    exporter = InMemorySpanExporter()
    config = _config(tmp_path)
    handle = _handle(config, exporter)
    with TestClient(hub_app.build_hosted_app(config, platform_tracing=handle)) as client:
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
    with TestClient(hub_app.build_hosted_app(config, platform_tracing=handle)) as client:
        client.get("/api/chunks", headers=_TRACEPARENT)
    server = _server_span(_spans(handle, exporter), "GET /api/chunks")
    assert server.parent is None
    assert server.context.trace_id != _PLANTED_TRACE


_POLLING_GRAPH_YAML = """
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
    poll_interval: 1
    poll_timeout: 600
    run:
      - name: prepare
        command: "true"
        produces: prepared
      - command: "if [ -f polled ]; then echo success; else touch polled; echo pending; fi"
    judgement:
      choices:
        success:
          description: Landed.
          to: done
        failure:
          description: Failed.
          to: build
"""


def test_hub_run_steps_parent_on_the_derived_hub_exec_span_and_the_driving_request_links_it(
    tmp_path: Path,
) -> None:
    from sqlalchemy import select

    from blizzard.foundation.trace_ids import DerivedContext, SpanRole, StepKey
    from blizzard.hub.domain.observability.tracing.platform import RUN_STEP_EXIT_CODE, RUN_STEP_NAME, RUN_STEP_SPAN
    from blizzard.hub.store import schema as s

    exporter = InMemorySpanExporter()
    config = _config(tmp_path)
    handle = _handle(config, exporter)
    app = hub_app.build_hosted_app(config, platform_tracing=handle)
    with RunnerFleetClient(app, services=app.state.services) as client:
        assert client.post("/api/graphs", json={"definition_yaml": _POLLING_GRAPH_YAML}).status_code == 201
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
        time.sleep(1.1)
        advanced = client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")
        assert advanced.json()["outcome_choice"] == "success", advanced.text
    with app.state.engine.connect() as conn:
        slots = conn.execute(
            select(s.hub_exec_slot.c.slot_id)
            .where(s.hub_exec_slot.c.holder_chunk_id == chunk_id)
            .order_by(s.hub_exec_slot.c.acquired_at)
        ).all()
    spans = _spans(handle, exporter)
    assert len(slots) == 2
    hub_execs = [DerivedContext.of(StepKey.attempt(chunk_id, 2), SpanRole.HUB_EXEC, row.slot_id) for row in slots]

    drivers = [
        _server_span(spans, f"POST /api/fleet/chunks/{{chunk_id}}/{verb}") for verb in ("completions", "hub-advance")
    ]
    for driver, hub_exec in zip(drivers, hub_execs, strict=True):
        assert [(link.context.trace_id, link.context.span_id) for link in driver.links] == [
            (hub_exec.trace_id, hub_exec.span_id)
        ]

    run_steps = [s for s in spans if s.name == RUN_STEP_SPAN]
    by_parent: dict[int, list] = {}  # type: ignore[type-arg]
    for span in run_steps:
        assert span.context.trace_id == span.parent.trace_id
        by_parent.setdefault(span.parent.span_id, []).append(span)
    first, second = (by_parent[h.span_id] for h in hub_execs)
    assert all(span.context.trace_id == hub_execs[0].trace_id for span in run_steps)
    assert [dict(span.attributes or {}) for span in first] == [
        {RUN_STEP_NAME: "prepare", RUN_STEP_EXIT_CODE: 0},
        {RUN_STEP_EXIT_CODE: 0},
    ]
    assert [dict(span.attributes or {}) for span in second] == [{RUN_STEP_EXIT_CODE: 0}]
    assert "polled" not in repr([dict(s.attributes or {}) for s in spans if s.name == RUN_STEP_SPAN])
    assert not [s for s in spans if "exec" in s.name and s.name != RUN_STEP_SPAN]


def test_an_operator_command_span_parents_the_hubs_server_span_for_an_authenticated_operator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI's root span and the hub's server span share one trace, the hub's the child — through
    ``CliContext`` over the app's own transport, with an operator session under a real auth mode."""
    exporter = InMemorySpanExporter()
    config = replace(_config(tmp_path), auth=AuthConfig(mode=AUTH_MODE_OAUTH))
    handle = _handle(config, exporter)
    app = hub_app.build_hosted_app(config, platform_tracing=handle)
    posted: list[httpx.Request] = []
    cli_exporter = httpx.Client(transport=httpx.MockTransport(lambda r: posted.append(r) or httpx.Response(200)))
    with TestClient(app) as client:
        harness = SimpleNamespace(engine=app.state.engine, clock=FixedClock(datetime(2026, 7, 13, tzinfo=UTC)))
        session = app.state.services.auth.mint_session(
            seed_user(harness, username="op", role=Role.CONTRIBUTOR, email="op@example.com")  # type: ignore[arg-type]
        )[0]
        SessionFile.of().save("http://testserver", session)
        monkeypatch.setattr(
            httpx,
            "get",
            lambda url, *, headers=None, params=None, timeout=None: client.get(
                url.removeprefix("http://testserver"), headers=headers, params=params
            ),
        )
        result = CliRunner().invoke(
            hub_group,
            ["chunk", "list"],
            env={"BZ_HUB_URL": "http://testserver", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector.local:4318"},
            obj=CliCollaborators(client_factory=lambda: cli_exporter, clock=Clock()),
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
    app = hub_app.build_hosted_app(config, platform_tracing=handle)
    with TestClient(app) as client:
        services = app.state.services
        harness = SimpleNamespace(engine=app.state.engine, clock=FixedClock(datetime(2026, 7, 13, tzinfo=UTC)))
        user = seed_user(cast(Any, harness), username="op", role=Role.CONTRIBUTOR, email="op@example.com")
        session = services.auth.mint_session(user)[0]
        calls: list[str] = []
        lookup, touch = services.sessions.get_by_hash, services.auth.touch_session
        monkeypatch.setattr(services.sessions, "get_by_hash", lambda h: calls.append("get_by_hash") or lookup(h))
        monkeypatch.setattr(services.auth, "touch_session", lambda s: calls.append("touch_session") or touch(s))
        response = client.get(
            "/api/chunks",
            headers={"Authorization": f"Bearer {session}", "traceparent": f"00-{'a' * 32}-{'b' * 16}-01"},
        )
    assert response.status_code == 200, response.text
    assert calls == ["get_by_hash", "touch_session"]
