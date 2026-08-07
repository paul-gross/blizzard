"""The runner-local fleet-summary pass-through proxy — ``GET /api/fleet-summary`` (issue #76).

Proves the runner's half of the forward: it uses the loop's own bearer credential, a
hub outage surfaces as a distinct error rather than empty counts, and an unwired runner
503s instead of pretending."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import blizzard.runner.api.hub_proxy as hub_proxy
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig

_HUB_URL = "http://hub.local:8421"
_COUNTS: dict[str, object] = {"ready": 4, "running": 3, "waiting": 2, "needs": 1}


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


def _runner_app(tmp_path: Path, *, hub_url: str | None = _HUB_URL, hub_token: str = "") -> TestClient:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url=hub_url or "", hub_token=hub_token)
    return TestClient(create_app(config))


@pytest.mark.component
def test_proxy_forwards_the_read_to_the_hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The route forwards to the hub's fleet-summary route and returns the counts verbatim."""
    seen: list[str] = []

    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        seen.append(url)
        return _FakeHubResponse(200, _COUNTS)

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    resp = _runner_app(tmp_path).get("/api/fleet-summary")

    assert resp.status_code == 200, resp.text
    assert resp.json() == _COUNTS
    assert seen == [f"{_HUB_URL}/api/fleet/summary"]


@pytest.mark.component
def test_proxy_forwards_the_authorization_header_when_a_token_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The forward carries the same bearer credential as the loop's own hub client."""
    seen_headers: list[dict[str, str]] = []

    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        seen_headers.append(dict(headers))
        return _FakeHubResponse(200, _COUNTS)

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    resp = _runner_app(tmp_path, hub_token="proxy-token").get("/api/fleet-summary")

    assert resp.status_code == 200, resp.text
    assert seen_headers == [{"Authorization": "Bearer proxy-token"}]


@pytest.mark.component
def test_proxy_sends_no_authorization_header_when_no_token_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ``hub_token`` (unenrolled runner) is a valid warn-mode state: no header at all."""
    seen_headers: list[dict[str, str]] = []

    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        seen_headers.append(dict(headers))
        return _FakeHubResponse(200, _COUNTS)

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    resp = _runner_app(tmp_path).get("/api/fleet-summary")

    assert resp.status_code == 200, resp.text
    assert seen_headers == [{}]


@pytest.mark.component
def test_proxy_passes_through_the_hub_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A hub 5xx surfaces with the hub's own status — a distinct error, not empty counts."""

    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        return _FakeHubResponse(500, {"detail": "hub store error"})

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    resp = _runner_app(tmp_path).get("/api/fleet-summary")

    assert resp.status_code == 500
    assert resp.json()["detail"] == "hub store error"


@pytest.mark.component
def test_proxy_502_when_the_hub_is_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A transport failure to the hub is a 502 — never a pretend answer of empty counts."""

    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    resp = _runner_app(tmp_path).get("/api/fleet-summary")

    assert resp.status_code == 502
    assert "unreachable" in resp.json()["detail"]


@pytest.mark.component
def test_proxy_503_when_the_runner_is_not_wired_to_a_hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``hub_url`` (never enrolled) 503s before any outbound call."""
    attempted = False

    def fake_request(*args: object, **kwargs: object) -> _FakeHubResponse:
        nonlocal attempted
        attempted = True
        return _FakeHubResponse(200, _COUNTS)

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    resp = _runner_app(tmp_path, hub_url=None).get("/api/fleet-summary")

    assert resp.status_code == 503
    assert attempted is False
