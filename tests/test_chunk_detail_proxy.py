"""The runner-local chunk-detail pass-through proxy (issue #185) — route + forward shape.

Proves the *runner's* half of pause/resume: the runner route over a real app, the hub
reached through a stubbed ``httpx.Client``, and the 202/404/409 + 502-on-unreachable
pass-through. The hub half (the domain refusal, the pause fact) is ``test_chunks_api.py``'s.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from tests.runner_fakes import no_retry_delay

_HUB_URL = "http://hub.local:8421"
_CHUNK = "ch_pass"
_DETAIL: dict[str, object] = {
    "chunk_id": _CHUNK,
    "graph_id": "gr_1",
    "status": "running",
    "current_node_id": "nd_build",
    "current_node_name": "build",
    "latest_epoch": 1,
    "work_refs": [{"source": "widget", "ref": "42", "label": "widget#42", "web_url": "http://forge.local/42"}],
    "pause": None,
    "history": [
        {
            "from_node_id": "nd_plan",
            "from_node_name": "plan",
            "to_node_id": "nd_build",
            "to_node_name": "build",
            "choice_name": "acceptable",
            "epoch": 1,
            "recorded_at": "2026-08-16T00:00:00Z",
        }
    ],
    "artifacts": [
        {
            "key": "build.retrospective.1",
            "kind": "asset",
            "name": "retrospective",
            "node_id": "nd_build",
            "node_name": "build",
            "epoch": 1,
            "content": "went fine",
        }
    ],
    "escalation": {"epoch": 1, "takeover_command": "blizzard runner takeover ch_pass"},
}
_SUMMARY: dict[str, object] = {
    "chunk_id": _CHUNK,
    "graph_id": "gr_1",
    "status": "waiting_on_human",
    "current_node_id": "nd_build",
    "current_node_name": "build",
    "runner_id": None,
    "environment_count": 0,
}


def _runner_app(tmp_path: Path, *, hub_proxy_client: httpx.Client | None = None) -> TestClient:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url=_HUB_URL)
    return TestClient(create_app(config, hub_proxy_client=hub_proxy_client, hub_retry_delay=no_retry_delay))


@pytest.mark.component
def test_get_chunk_forwards_to_the_fleet_route(tmp_path: Path) -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        return httpx.Response(200, json=_DETAIL)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resp = _runner_app(tmp_path, hub_proxy_client=client).get(f"/api/chunks/{_CHUNK}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["chunk_id"] == _CHUNK
    assert body["work_refs"][0]["web_url"] == "http://forge.local/42"
    assert body["history"][0]["to_node_name"] == "build"
    assert body["artifacts"][0]["content"] == "went fine"
    assert body["escalation"]["takeover_command"] == "blizzard runner takeover ch_pass"
    assert seen == [("GET", f"{_HUB_URL}/api/fleet/chunks/{_CHUNK}")]


@pytest.mark.component
def test_get_chunk_passes_through_a_hub_404(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "unknown chunk ch_pass"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resp = _runner_app(tmp_path, hub_proxy_client=client).get(f"/api/chunks/{_CHUNK}")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "unknown chunk ch_pass"


@pytest.mark.component
def test_get_chunk_502_when_the_hub_is_unreachable(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resp = _runner_app(tmp_path, hub_proxy_client=client).get(f"/api/chunks/{_CHUNK}")

    assert resp.status_code == 502
    assert "unreachable" in resp.json()["detail"]


@pytest.mark.component
@pytest.mark.parametrize("verb", ["pause", "resume"])
def test_pause_and_resume_forward_to_the_fleet_route(tmp_path: Path, verb: str) -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        return httpx.Response(202, json=_SUMMARY)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resp = _runner_app(tmp_path, hub_proxy_client=client).post(f"/api/chunks/{_CHUNK}/{verb}")

    assert resp.status_code == 202, resp.text
    assert resp.json()["chunk_id"] == _CHUNK
    assert seen == [("POST", f"{_HUB_URL}/api/fleet/chunks/{_CHUNK}/{verb}")]


@pytest.mark.component
def test_pause_passes_through_a_hub_409(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "chunk ch_pass is not pausable"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resp = _runner_app(tmp_path, hub_proxy_client=client).post(f"/api/chunks/{_CHUNK}/pause")

    assert resp.status_code == 409
    assert resp.json()["detail"] == "chunk ch_pass is not pausable"


@pytest.mark.component
def test_proxy_forwards_the_authorization_header_when_a_token_is_configured(tmp_path: Path) -> None:
    seen_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers)
        return httpx.Response(200, json=_DETAIL)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url=_HUB_URL, hub_token="proxy-token")
    resp = TestClient(create_app(config, hub_proxy_client=client)).get(f"/api/chunks/{_CHUNK}")

    assert resp.status_code == 200, resp.text
    assert seen_headers[0]["Authorization"] == "Bearer proxy-token"


@pytest.mark.component
def test_503_when_the_runner_is_not_wired_to_a_hub(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url="")
    resp = TestClient(create_app(config)).get(f"/api/chunks/{_CHUNK}")

    assert resp.status_code == 503
