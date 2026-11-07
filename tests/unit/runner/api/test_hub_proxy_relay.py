"""``HubProxy`` relays a hub refusal to the runner's caller as the hub sent it."""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
from fastapi import FastAPI
from fastapi.testclient import TestClient

from blizzard.foundation.clock import ManualMonotonicClock
from blizzard.runner.api.hub_proxy import HubProxy, UpstreamRefusal, relay_upstream_refusal
from blizzard.runner.config import RunnerConfig
from blizzard.wire.problem import CHUNK_NOT_FOUND, SYSTEM_ARTIFACT_NOT_FOUND

_PROBLEM = {
    "type": CHUNK_NOT_FOUND,
    "title": "Chunk not found",
    "detail": "no such chunk ch_1",
    "instance": "/api/fleet/chunks/ch_1",
    "status": 404,
}


def _client_for(tmp_path: Path, upstream: httpx2.Response) -> TestClient:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url="http://hub.local")
    proxy = HubProxy(
        config, "test", httpx2.Client(transport=httpx2.MockTransport(lambda _: upstream)), ManualMonotonicClock()
    )
    app = FastAPI()
    app.add_exception_handler(UpstreamRefusal, relay_upstream_refusal)

    @app.get("/relay")
    def relay() -> dict[str, object]:  # pyright: ignore[reportUnusedFunction]
        return proxy.get("/api/fleet/chunks/ch_1").json()

    return TestClient(app)


def test_a_hub_problem_reaches_the_caller_byte_identical_with_its_media_and_headers(tmp_path: Path) -> None:
    body = json.dumps(_PROBLEM).encode()
    upstream = httpx2.Response(
        404, content=body, headers={"content-type": "application/problem+json", "retry-after": "3"}
    )

    resp = _client_for(tmp_path, upstream).get("/relay")

    assert (resp.status_code, resp.content) == (404, body)
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.headers["retry-after"] == "3"


def test_a_legacy_body_passes_through_unchanged(tmp_path: Path) -> None:
    body = json.dumps({"detail": {"code": "flag_off", "message": "off"}}).encode()

    resp = _client_for(tmp_path, httpx2.Response(404, content=body, headers={"content-type": "application/json"})).get(
        "/relay"
    )

    assert resp.content == body


def test_a_refusal_with_no_json_body_keeps_the_text_as_a_detail(tmp_path: Path) -> None:
    resp = _client_for(tmp_path, httpx2.Response(500, content=b"boom")).get("/relay")

    assert resp.status_code == 500
    assert resp.json() == {"detail": "boom"}


def test_the_relayed_type_is_what_a_route_dispatches_on(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url="http://hub.local")
    typed = httpx2.Response(404, json={**_PROBLEM, "type": SYSTEM_ARTIFACT_NOT_FOUND})
    legacy = httpx2.Response(404, json={"detail": "x"})
    kinds: list[str | None] = []
    for upstream in (typed, legacy):
        proxy = HubProxy(
            config,
            "test",
            httpx2.Client(transport=httpx2.MockTransport(lambda _, u=upstream: u)),
            ManualMonotonicClock(),
        )
        try:
            proxy.get("/api/fleet/system-artifacts/x")
        except UpstreamRefusal as exc:
            kinds.append(exc.problem_type)

    assert kinds == [SYSTEM_ARTIFACT_NOT_FOUND, None]
