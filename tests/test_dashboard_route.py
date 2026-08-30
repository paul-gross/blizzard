"""The composed dashboard read — ``GET /api/dashboard`` (issue #311).

Proves the six local sections populate the same way their own individual routes do,
``fleet_summary`` alone degrades to ``None`` on a hub outage or an unwired runner, and
this route's own hub call carries a bounded, below-the-poll-floor timeout distinct from
``/api/fleet-summary``'s untouched 15s default."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

import blizzard.runner.api.hub_proxy as hub_proxy
from blizzard.foundation.clock import FixedClock
from blizzard.runner.api.dashboard import _DASHBOARD_HUB_TIMEOUT
from blizzard.runner.api.hub_proxy import _HUB_TIMEOUT
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.domain.status import RunnerStatusService
from blizzard.runner.harness.adapter import WorkerHandle
from tests.runner_fakes import FakeHarness, make_store, make_stores

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
_HUB_URL = "http://hub.local:8421"
_COUNTS: dict[str, object] = {"ready": 4, "running": 3, "waiting": 2, "needs": 1}


class _FakeHubResponse:
    def __init__(self, status_code: int, payload: dict[str, object] | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


def _app_with_status(tmp_path: Path, *, hub_url: str | None = _HUB_URL) -> tuple[TestClient, object]:  # type: ignore[type-arg]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", hub_url=hub_url or "")
    harness = FakeHarness(handle=WorkerHandle(session_id="sess-x", pid=1, process_start_time="start-1"), verdict=None)
    service = RunnerStatusService(
        make_stores(store),
        FixedClock(_NOW),
        harness,
        runner_id=config.runner_id,
        workspace_id=config.workspace_id,
        max_agents=config.max_agents,
        hub_url=config.hub_url,
        env_pool=("e1",),
    )
    app = create_app(config, runner_stores=make_stores(store), runner_status=service)
    return TestClient(app), store


def _seed_lease(store, **overrides: object) -> None:  # type: ignore[no-untyped-def]
    fields: dict[str, object] = {
        "lease_id": "lease_1",
        "chunk_id": "ch_1",
        "graph_id": "gr_1",
        "node_id": "nd_build",
        "node_name": "build",
        "epoch": 1,
        "runner_id": "runner-local",
        "retries_max": 2,
        "created_at": _NOW,
    }
    fields.update(overrides)
    store.record_lease(NewLease(**fields))  # type: ignore[arg-type]


def _seed_all_sections(store) -> None:  # type: ignore[no-untyped-def]
    """Puts real data behind each of the six local sections."""
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    _seed_lease(store)
    store.record_ask(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="which branch?",
        options=["main", "dev"],
        session_id="sess-a",
        asked_at=_NOW,
    )
    store.enqueue_outbound(kind="lease.minted", chunk_id="ch_1", lease_id="lease_1", payload="{}", created_at=_NOW)
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id=None,
        session_id="sess-a",
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )
    _seed_lease(store, lease_id="lease_2", chunk_id="ch_2", epoch=1)
    store.record_spawn("lease_2", pid=200, process_start_time="start-200", session_id="sess-b", spawned_at=_NOW)
    store.record_binding(chunk_id="ch_2", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_closure(
        lease_id="lease_2",
        chunk_id="ch_2",
        node_id="nd_build",
        reason="escalated",
        closed_at=_NOW + timedelta(minutes=5),
    )


@pytest.mark.component
def test_the_composed_payload_includes_all_seven_sections_with_real_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store = _app_with_status(tmp_path)
    _seed_all_sections(store)

    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        return _FakeHubResponse(200, _COUNTS)

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    resp = client.get("/api/dashboard")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["runner"]["runner_id"] == "runner-local"
    assert [e["chunk_id"] for e in body["environments"]["items"]] == ["ch_1", "ch_2"]
    assert [a["question_id"] for a in body["asks"]["items"]] == ["qn_1"]
    assert [e["chunk_id"] for e in body["escalations"]["items"]] == ["ch_2"]
    assert [t["takeover_id"] for t in body["takeovers"]["items"]] == ["tko_1"]
    assert [f["kind"] for f in body["facts"]["items"]] == ["lease.minted"]
    assert body["fleet_summary"] == _COUNTS


@pytest.mark.component
def test_fleet_summary_is_none_on_a_hub_outage_and_the_six_local_sections_still_populate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store = _app_with_status(tmp_path)
    _seed_all_sections(store)

    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    resp = client.get("/api/dashboard")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fleet_summary"] is None
    assert body["environments"]["items"] != []
    assert body["asks"]["items"] != []
    assert body["escalations"]["items"] != []
    assert body["takeovers"]["items"] != []
    assert body["facts"]["items"] != []


@pytest.mark.component
def test_fleet_summary_is_none_when_the_runner_is_unwired_to_a_hub_and_the_six_local_sections_still_populate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store = _app_with_status(tmp_path, hub_url=None)
    _seed_all_sections(store)
    attempted = False

    def fake_request(*args: object, **kwargs: object) -> _FakeHubResponse:
        nonlocal attempted
        attempted = True
        return _FakeHubResponse(200, _COUNTS)

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    resp = client.get("/api/dashboard")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fleet_summary"] is None
    assert attempted is False
    assert body["environments"]["items"] != []
    assert body["asks"]["items"] != []
    assert body["escalations"]["items"] != []
    assert body["takeovers"]["items"] != []
    assert body["facts"]["items"] != []


@pytest.mark.component
def test_the_dashboards_own_hub_call_carries_the_bounded_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The composed route's own outbound call is bounded well below a human-tolerable
    read latency — distinct from ``/api/fleet-summary``'s own call, which keeps the
    module default (proven by ``test_fleet_summary_proxy.py``)."""
    client, store = _app_with_status(tmp_path)
    _seed_all_sections(store)
    seen_timeouts: list[float] = []

    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        seen_timeouts.append(timeout)
        return _FakeHubResponse(200, _COUNTS)

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    resp = client.get("/api/dashboard")

    assert resp.status_code == 200, resp.text
    assert seen_timeouts == [_DASHBOARD_HUB_TIMEOUT]
    assert _DASHBOARD_HUB_TIMEOUT < 5.0
    assert _DASHBOARD_HUB_TIMEOUT < _HUB_TIMEOUT


@pytest.mark.component
def test_the_dashboards_own_unreachable_hub_line_logs_below_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hub outage here is tolerated degradation — the six local sections still stand
    (issue #374) — so this route's own unreachable-hub line logs below the module
    default ``error``, distinct from ``/api/fleet-summary``'s own call, which keeps it
    (proven by ``test_fleet_summary_proxy.py``)."""
    client, store = _app_with_status(tmp_path)
    _seed_all_sections(store)

    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    with capture_logs() as logs:
        resp = client.get("/api/dashboard")

    assert resp.status_code == 200, resp.text
    unreachable = [entry for entry in logs if entry["event"] == "dashboard proxy could not reach the hub"]
    assert len(unreachable) == 1
    assert unreachable[0]["log_level"] == "warning"
