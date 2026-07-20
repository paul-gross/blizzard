"""The runner-local fleet-summary pass-through proxy — ``GET /api/fleet-summary`` (issue #76).

The layered pass-through behind the machine panel's counts strip: the panel reads the
fleet's four bucket counts through the runner's own route, which **forwards** to the hub's
fleet-router summary (``/api/fleet/summary``) — the browser never crosses to the hub
directly. The hub half (the fold) is covered by ``test_fleet_summary_api``; this proves the
*runner's* half — that it forwards with the loop's own bearer credential, that a hub outage
surfaces as a distinct error the strip degrades on rather than empty counts, and that an
unwired runner 503s instead of pretending.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import blizzard.runner.api.fleet_summary as fleet_summary_route
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

    def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        seen.append(url)
        return _FakeHubResponse(200, _COUNTS)

    monkeypatch.setattr(fleet_summary_route.httpx, "get", fake_get)
    resp = _runner_app(tmp_path).get("/api/fleet-summary")

    assert resp.status_code == 200, resp.text
    assert resp.json() == _COUNTS
    # It forwarded to the hub's fleet-router summary — the panel never crosses a layer.
    assert seen == [f"{_HUB_URL}/api/fleet/summary"]


@pytest.mark.component
def test_proxy_forwards_the_authorization_header_when_a_token_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The forward carries the same bearer credential as the loop's own hub client."""
    seen_headers: list[dict[str, str]] = []

    def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        seen_headers.append(dict(headers))
        return _FakeHubResponse(200, _COUNTS)

    monkeypatch.setattr(fleet_summary_route.httpx, "get", fake_get)
    resp = _runner_app(tmp_path, hub_token="proxy-token").get("/api/fleet-summary")

    assert resp.status_code == 200, resp.text
    assert seen_headers == [{"Authorization": "Bearer proxy-token"}]


@pytest.mark.component
def test_proxy_sends_no_authorization_header_when_no_token_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ``hub_token`` (unenrolled runner) is a valid warn-mode state: no header at all."""
    seen_headers: list[dict[str, str]] = []

    def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        seen_headers.append(dict(headers))
        return _FakeHubResponse(200, _COUNTS)

    monkeypatch.setattr(fleet_summary_route.httpx, "get", fake_get)
    resp = _runner_app(tmp_path).get("/api/fleet-summary")

    assert resp.status_code == 200, resp.text
    assert seen_headers == [{}]


@pytest.mark.component
def test_proxy_passes_through_the_hub_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A hub 5xx surfaces with the hub's own status — a distinct error, not empty counts,
    so the strip degrades to its last-known/dimmed state on the real reason."""

    def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        return _FakeHubResponse(500, {"detail": "hub store error"})

    monkeypatch.setattr(fleet_summary_route.httpx, "get", fake_get)
    resp = _runner_app(tmp_path).get("/api/fleet-summary")

    assert resp.status_code == 500
    assert resp.json()["detail"] == "hub store error"


@pytest.mark.component
def test_proxy_502_when_the_hub_is_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A transport failure to the hub is a 502 — never a pretend answer of empty counts."""

    def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(fleet_summary_route.httpx, "get", fake_get)
    resp = _runner_app(tmp_path).get("/api/fleet-summary")

    assert resp.status_code == 502
    assert "unreachable" in resp.json()["detail"]


@pytest.mark.component
def test_proxy_503_when_the_runner_is_not_wired_to_a_hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``hub_url`` (never enrolled) 503s before any outbound call — the strip degrades,
    the rest of the panel stays lit."""
    attempted = False

    def fake_get(*args: object, **kwargs: object) -> _FakeHubResponse:
        nonlocal attempted
        attempted = True
        return _FakeHubResponse(200, _COUNTS)

    monkeypatch.setattr(fleet_summary_route.httpx, "get", fake_get)
    resp = _runner_app(tmp_path, hub_url=None).get("/api/fleet-summary")

    assert resp.status_code == 503
    assert attempted is False
