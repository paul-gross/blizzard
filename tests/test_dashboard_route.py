"""The composed dashboard read — ``GET /runners/{runner_id}/api/dashboard``.

Proves the six local sections populate the same way their own individual routes do,
``fleet_summary`` alone degrades to ``None`` on a hub outage or an unwired runner, and
this route's own hub call carries a bounded, below-the-poll-floor timeout distinct from
``/api/fleet-summary``'s untouched 15s default."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Unpack

import httpx2
import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock
from blizzard.runner.api.dashboard import _DASHBOARD_HUB_TIMEOUT
from blizzard.runner.api.hub_proxy import _HUB_TIMEOUT
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.leases.model import NewLease
from blizzard.runner.status.view import RunnerStatusService
from tests.config_inputs import create_runner_app
from tests.factory_fields import NewLeaseFields
from tests.runner_client import runner_client
from tests.runner_fakes import (
    FakeHarness,
    FakeHarnessHealth,
    FakeProvider,
    SqlAlchemyRunnerStore,
    make_host_stores,
    make_store,
    make_stores,
    no_retry_clock,
    registered_identity,
    single_workspace,
)

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
_HUB_URL = "http://hub.local:8421"
_COUNTS: dict[str, object] = {"ready": 4, "running": 3, "waiting": 2, "needs": 1}


def _app_with_status(
    tmp_path: Path, *, hub_url: str | None = _HUB_URL, hub_proxy_client: httpx2.Client | None = None
) -> tuple[TestClient, SqlAlchemyRunnerStore]:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", hub_url=hub_url or "")
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-x", pid=1, process_start_time="start-1", pgid=1), verdict=None
    )
    service = RunnerStatusService(
        FixedClock(_NOW),
        pause=store,
        daemon_liveness=store,
        lease_record=store,
        outbound=store,
        environments=store,
        asks=store,
        takeover=store,
        escalations=store,
        identity=registered_identity("rn_status", config.name),
        runner_name=config.name,
        workspace_id=config.home_workspace.id,
        hub_url=config.hub_url,
        workspaces=single_workspace(FakeProvider({})),
        harnesses=HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness)}),
    )
    app = create_runner_app(
        config,
        runner_stores=make_stores(store),
        host_stores=make_host_stores(store),
        runner_status=service,
        harness_health=FakeHarnessHealth(),
        hub_proxy_client=hub_proxy_client,
        hub_retry_clock=no_retry_clock(),
    )
    return runner_client(app), store


def _seed_lease(store: SqlAlchemyRunnerStore, **overrides: Unpack[NewLeaseFields]) -> None:
    fields: NewLeaseFields = {
        "lease_id": "lease_1",
        "chunk_id": "ch_1",
        "graph_id": "gr_1",
        "node_id": "nd_build",
        "node_name": "build",
        "epoch": 1,
        "retries_max": 2,
        "created_at": _NOW,
    }
    fields.update(overrides)
    store.record_lease(NewLease(**fields))


def _seed_all_sections(store: SqlAlchemyRunnerStore) -> None:
    """Puts real data behind each of the seven local sections."""
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    _seed_lease(store)
    store.record_ask(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="which branch?",
        options=["main", "dev"],
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        asked_at=_NOW,
    )
    store.enqueue_outbound(kind="lease.minted", chunk_id="ch_1", lease_id="lease_1", payload="{}", created_at=_NOW)
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id=None,
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )
    _seed_lease(store, lease_id="lease_2", chunk_id="ch_2", epoch=1)
    store.record_spawn(
        "lease_2",
        pid=200,
        process_start_time="start-200",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-b"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id="ch_2", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_closure(
        lease_id="lease_2",
        chunk_id="ch_2",
        node_id="nd_build",
        reason="escalated",
        closed_at=_NOW + timedelta(minutes=5),
    )


@pytest.mark.component
def test_the_composed_payload_includes_all_seven_sections_with_real_data(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_COUNTS)

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    test_client, store = _app_with_status(tmp_path, hub_proxy_client=client)
    _seed_all_sections(store)

    resp = test_client.get("/api/dashboard")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["runner"]["runner_id"], body["runner"]["runner_name"]) == ("rn_status", "runner-local")
    assert [a["question_id"] for a in body["asks"]["items"]] == ["qn_1"]
    assert [e["chunk_id"] for e in body["escalations"]["items"]] == ["ch_2"]
    assert [t["takeover_id"] for t in body["takeovers"]["items"]] == ["tko_1"]
    assert [f["kind"] for f in body["facts"]["items"]] == ["lease.minted"]
    # No harness is wired onto `create_app` itself here (only `RunnerStatusService`'s own
    # registry above carries one) — an empty configured set reports no health entries.
    assert body["harness_health"] == {"items": []}
    # The host's environments and subscriptions are its own reads, not this runner's.
    assert "environments" not in body and "subscriptions" not in body
    assert body["fleet_summary"] == _COUNTS


@pytest.mark.component
def test_fleet_summary_is_none_on_a_hub_outage_and_the_local_sections_still_populate(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused")

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    test_client, store = _app_with_status(tmp_path, hub_proxy_client=client)
    _seed_all_sections(store)

    resp = test_client.get("/api/dashboard")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fleet_summary"] is None
    assert body["asks"]["items"] != []
    assert body["escalations"]["items"] != []
    assert body["takeovers"]["items"] != []
    assert body["facts"]["items"] != []


@pytest.mark.component
def test_fleet_summary_is_none_when_the_runner_is_unwired_to_a_hub_and_the_local_sections_still_populate(
    tmp_path: Path,
) -> None:
    attempted = False

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempted
        attempted = True
        return httpx2.Response(200, json=_COUNTS)

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    test_client, store = _app_with_status(tmp_path, hub_url=None, hub_proxy_client=client)
    _seed_all_sections(store)

    resp = test_client.get("/api/dashboard")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fleet_summary"] is None
    assert attempted is False
    assert body["asks"]["items"] != []
    assert body["escalations"]["items"] != []
    assert body["takeovers"]["items"] != []
    assert body["facts"]["items"] != []


@pytest.mark.component
def test_the_dashboards_own_hub_call_carries_the_bounded_timeout(tmp_path: Path) -> None:
    """The composed route's own outbound call is bounded well below a human-tolerable
    read latency — distinct from ``/api/fleet-summary``'s own call, which keeps the
    module default (proven by ``tests/test_fleet_summary_proxy.py``)."""
    seen_timeouts: list[float] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen_timeouts.append(request.extensions.get("timeout", {}).get("pool"))
        return httpx2.Response(200, json=_COUNTS)

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    test_client, store = _app_with_status(tmp_path, hub_proxy_client=client)
    _seed_all_sections(store)

    resp = test_client.get("/api/dashboard")

    assert resp.status_code == 200, resp.text
    assert seen_timeouts == [_DASHBOARD_HUB_TIMEOUT]
    assert _DASHBOARD_HUB_TIMEOUT < 5.0
    assert _DASHBOARD_HUB_TIMEOUT < _HUB_TIMEOUT


@pytest.mark.component
def test_the_dashboards_own_unreachable_hub_line_logs_below_error(tmp_path: Path) -> None:
    """A hub outage here is tolerated degradation — the eight local sections still stand —
    so this route's own unreachable-hub line logs below the module
    default ``error``, distinct from ``/api/fleet-summary``'s own call, which keeps it
    (proven by ``tests/test_fleet_summary_proxy.py``)."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused")

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    test_client, store = _app_with_status(tmp_path, hub_proxy_client=client)
    _seed_all_sections(store)

    with capture_logs() as logs:
        resp = test_client.get("/api/dashboard")

    assert resp.status_code == 200, resp.text
    unreachable = [entry for entry in logs if entry["event"] == "dashboard proxy could not reach the hub"]
    assert len(unreachable) == 1
    assert unreachable[0]["log_level"] == "warning"
