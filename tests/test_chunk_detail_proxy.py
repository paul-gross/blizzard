"""The runner-local chunk-detail pass-through proxy (issue #185) — route + forward shape.

Proves the *runner's* half of pause/resume: the runner route over a real app, the hub
reached through a stubbed ``httpx.request``, and the 202/404/409 + 502-on-unreachable
pass-through. The hub half (the domain refusal, the pause fact) is ``test_chunks_api.py``'s.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import blizzard.runner.api.hub_proxy as hub_proxy
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig

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


class _FakeHubResponse:
    """A stand-in for the hub's ``httpx.Response`` on the proxy's outbound edge."""

    def __init__(self, status_code: int, payload: dict[str, object] | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


def _runner_app(tmp_path: Path) -> TestClient:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url=_HUB_URL)
    return TestClient(create_app(config))


@pytest.mark.component
def test_get_chunk_forwards_to_the_fleet_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str]] = []

    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        seen.append((method, url))
        return _FakeHubResponse(200, _DETAIL)

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    resp = _runner_app(tmp_path).get(f"/api/chunks/{_CHUNK}")

    assert resp.status_code == 200, resp.text
    assert resp.json()["chunk_id"] == _CHUNK
    assert resp.json()["work_refs"][0]["web_url"] == "http://forge.local/42"
    assert seen == [("GET", f"{_HUB_URL}/api/fleet/chunks/{_CHUNK}")]


@pytest.mark.component
def test_get_chunk_passes_through_a_hub_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        return _FakeHubResponse(404, {"detail": "unknown chunk ch_pass"})

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    resp = _runner_app(tmp_path).get(f"/api/chunks/{_CHUNK}")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "unknown chunk ch_pass"


@pytest.mark.component
def test_get_chunk_502_when_the_hub_is_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    resp = _runner_app(tmp_path).get(f"/api/chunks/{_CHUNK}")

    assert resp.status_code == 502
    assert "unreachable" in resp.json()["detail"]


@pytest.mark.component
@pytest.mark.parametrize("verb", ["pause", "resume"])
def test_pause_and_resume_forward_to_the_fleet_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verb: str
) -> None:
    seen: list[tuple[str, str]] = []

    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        seen.append((method, url))
        return _FakeHubResponse(202, _SUMMARY)

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    resp = _runner_app(tmp_path).post(f"/api/chunks/{_CHUNK}/{verb}")

    assert resp.status_code == 202, resp.text
    assert resp.json()["chunk_id"] == _CHUNK
    assert seen == [("POST", f"{_HUB_URL}/api/fleet/chunks/{_CHUNK}/{verb}")]


@pytest.mark.component
def test_pause_passes_through_a_hub_409(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        return _FakeHubResponse(409, {"detail": "chunk ch_pass is not pausable"})

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    resp = _runner_app(tmp_path).post(f"/api/chunks/{_CHUNK}/pause")

    assert resp.status_code == 409
    assert resp.json()["detail"] == "chunk ch_pass is not pausable"


@pytest.mark.component
def test_proxy_forwards_the_authorization_header_when_a_token_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen_headers: list[dict[str, str]] = []

    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        seen_headers.append(dict(headers))
        return _FakeHubResponse(200, _DETAIL)

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url=_HUB_URL, hub_token="proxy-token")
    resp = TestClient(create_app(config)).get(f"/api/chunks/{_CHUNK}")

    assert resp.status_code == 200, resp.text
    assert seen_headers == [{"Authorization": "Bearer proxy-token"}]


@pytest.mark.component
def test_503_when_the_runner_is_not_wired_to_a_hub(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url="")
    resp = TestClient(create_app(config)).get(f"/api/chunks/{_CHUNK}")

    assert resp.status_code == 503
