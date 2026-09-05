"""The runner-local fleet-summary pass-through proxy — ``GET /api/fleet-summary`` (issue #76).

Proves the runner's half of the forward: it uses the loop's own bearer credential, a
hub outage surfaces as a distinct error rather than empty counts, and an unwired runner
503s instead of pretending."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from tests.runner_fakes import no_retry_delay

_HUB_URL = "http://hub.local:8421"
_COUNTS: dict[str, object] = {"ready": 4, "running": 3, "waiting": 2, "needs": 1}


def _runner_app(
    tmp_path: Path, *, hub_url: str | None = _HUB_URL, hub_token: str = "", hub_proxy_client: httpx.Client | None = None
) -> TestClient:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url=hub_url or "", hub_token=hub_token)
    return TestClient(create_app(config, hub_proxy_client=hub_proxy_client, hub_retry_delay=no_retry_delay))


@pytest.mark.component
def test_proxy_forwards_the_read_to_the_hub(tmp_path: Path) -> None:
    """The route forwards to the hub's fleet-summary route and returns the counts verbatim."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=_COUNTS)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resp = _runner_app(tmp_path, hub_proxy_client=client).get("/api/fleet-summary")

    assert resp.status_code == 200, resp.text
    assert resp.json() == _COUNTS
    assert seen == [f"{_HUB_URL}/api/fleet/summary"]


@pytest.mark.component
def test_proxy_forwards_the_authorization_header_when_a_token_is_configured(tmp_path: Path) -> None:
    """The forward carries the same bearer credential as the loop's own hub client."""
    seen_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers)
        return httpx.Response(200, json=_COUNTS)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resp = _runner_app(tmp_path, hub_token="proxy-token", hub_proxy_client=client).get("/api/fleet-summary")

    assert resp.status_code == 200, resp.text
    assert seen_headers[0]["Authorization"] == "Bearer proxy-token"


@pytest.mark.component
def test_proxy_sends_no_authorization_header_when_no_token_is_configured(tmp_path: Path) -> None:
    """No ``hub_token`` (unenrolled runner) is a valid warn-mode state: no header at all."""
    seen_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers)
        return httpx.Response(200, json=_COUNTS)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resp = _runner_app(tmp_path, hub_proxy_client=client).get("/api/fleet-summary")

    assert resp.status_code == 200, resp.text
    assert "Authorization" not in seen_headers[0]


@pytest.mark.component
def test_proxy_passes_through_the_hub_status(tmp_path: Path) -> None:
    """A hub 5xx surfaces with the hub's own status — a distinct error, not empty counts."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "hub store error"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resp = _runner_app(tmp_path, hub_proxy_client=client).get("/api/fleet-summary")

    assert resp.status_code == 500
    assert resp.json()["detail"] == "hub store error"


@pytest.mark.component
def test_proxy_502_when_the_hub_is_unreachable(tmp_path: Path) -> None:
    """A transport failure to the hub is a 502 — never a pretend answer of empty counts."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resp = _runner_app(tmp_path, hub_proxy_client=client).get("/api/fleet-summary")

    assert resp.status_code == 502
    assert "unreachable" in resp.json()["detail"]


@pytest.mark.component
def test_proxy_unreachable_hub_line_logs_at_error(tmp_path: Path) -> None:
    """This route's own forward keeps today's ``error`` severity (issue #374) — only the
    dashboard's tolerated fleet-summary call lowers it (``test_dashboard_route.py``)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with capture_logs() as logs:
        resp = _runner_app(tmp_path, hub_proxy_client=client).get("/api/fleet-summary")

    assert resp.status_code == 502
    unreachable = [entry for entry in logs if entry["event"] == "fleet-summary proxy could not reach the hub"]
    assert len(unreachable) == 1
    assert unreachable[0]["log_level"] == "error"


@pytest.mark.component
def test_proxy_503_when_the_runner_is_not_wired_to_a_hub(tmp_path: Path) -> None:
    """No ``hub_url`` (never enrolled) 503s before any outbound call."""
    attempted = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempted
        attempted = True
        return httpx.Response(200, json=_COUNTS)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resp = _runner_app(tmp_path, hub_url=None, hub_proxy_client=client).get("/api/fleet-summary")

    assert resp.status_code == 503
    assert attempted is False
