"""The runner-local machine-status routes — ``GET /runner`` / ``/environments`` /
``/asks`` / ``/escalations`` (issue #51).

Exercised over a real store via TestClient, mirroring ``tests/test_runner_leases_api.py``'s
convention. Hub-free but for the derived reachability read: nothing here reaches for
the hub or the forge — every route's shape, its empty and unwired forms, and the
derivation->wire mapping (capacities, hub reachability from staleness, open-ask
filtering, escalation supersession) are the point.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.foundation.clock import FixedClock
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.status import RunnerStatusService
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.store.repository import NewLease
from tests.runner_fakes import FakeHarness, make_store
from tests.support import assert_all_timestamps_utc

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _app_with_status(
    tmp_path: Path,
    *,
    clock: FixedClock | None = None,
    harness: FakeHarness | None = None,
    max_agents: int = 2,
):  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", max_agents=max_agents)
    _harness = harness or FakeHarness(
        handle=WorkerHandle(session_id="sess-x", pid=1, process_start_time="start-1"),
        verdict=None,
    )
    service = RunnerStatusService(
        store,
        clock or FixedClock(_NOW),
        _harness,
        runner_id=config.runner_id,
        workspace_id=config.workspace_id,
        max_agents=config.max_agents,
    )
    return create_app(config, runner_store=store, runner_status=service), store


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


# --------------------------------------------------------------------------- #
# GET /runner
# --------------------------------------------------------------------------- #


@pytest.mark.component
def test_summary_defaults_on_an_empty_store(tmp_path: Path) -> None:
    app, _store = _app_with_status(tmp_path, max_agents=3)
    with TestClient(app) as client:
        resp = client.get("/api/runner")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["runner_id"] == "runner-local"
    assert body["workspace_id"] == "workspace-local"
    assert body["pause"] == {"local": False, "hub": False, "effective": False}
    assert body["capacities"] == {"max_agents": 3, "used": 0, "free": 3}
    assert body["hub"] == {"reachable": False, "last_contact_at": None, "buffer_depth": 0}
    assert body["last_tick_at"] is None
    assert_all_timestamps_utc(body)


@pytest.mark.component
def test_503_when_status_service_unwired(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    with TestClient(create_app(config)) as client:
        for path in ("/api/runner", "/api/environments", "/api/escalations", "/api/takeovers"):
            assert client.get(path).status_code == 503, path


@pytest.mark.component
def test_capacities_reflect_active_leases_the_same_way_fill_counts_them(tmp_path: Path) -> None:
    app, store = _app_with_status(tmp_path, max_agents=2)
    _seed_lease(store, lease_id="lease_1", chunk_id="ch_1")

    with TestClient(app) as client:
        resp = client.get("/api/runner")

    assert resp.json()["capacities"] == {"max_agents": 2, "used": 1, "free": 1}


@pytest.mark.component
def test_hub_reachable_when_last_contact_is_within_the_staleness_threshold(tmp_path: Path) -> None:
    clock = FixedClock(_NOW)
    app, store = _app_with_status(tmp_path, clock=clock)
    store.set_hub_paused("runner-local", paused=False, at=_NOW - timedelta(minutes=1))

    with TestClient(app) as client:
        resp = client.get("/api/runner")

    hub = resp.json()["hub"]
    assert hub["reachable"] is True
    assert hub["last_contact_at"] == (_NOW - timedelta(minutes=1)).isoformat()


@pytest.mark.component
def test_hub_unreachable_when_last_contact_is_stale(tmp_path: Path) -> None:
    """The hub-down path: an old contact fact reads honestly as unreachable, not stale-true."""
    clock = FixedClock(_NOW)
    app, store = _app_with_status(tmp_path, clock=clock)
    store.set_hub_paused("runner-local", paused=False, at=_NOW - timedelta(hours=1))

    with TestClient(app) as client:
        resp = client.get("/api/runner")

    hub = resp.json()["hub"]
    assert hub["reachable"] is False
    assert hub["last_contact_at"] == (_NOW - timedelta(hours=1)).isoformat()  # still reported, just stale


@pytest.mark.component
def test_pause_states_reported_apart_and_effective_is_the_or(tmp_path: Path) -> None:
    app, store = _app_with_status(tmp_path)
    store.record_local_pause(
        "runner-local", paused=True, at=_NOW, by="alice", report_kind="runner.locally_paused", report_payload="{}"
    )

    with TestClient(app) as client:
        resp = client.get("/api/runner")

    assert resp.json()["pause"] == {"local": True, "hub": False, "effective": True}


@pytest.mark.component
def test_buffer_depth_counts_the_unacked_outbound_buffer(tmp_path: Path) -> None:
    app, store = _app_with_status(tmp_path)
    store.enqueue_outbound(kind="lease.minted", chunk_id="ch_1", lease_id="lease_1", payload="{}", created_at=_NOW)
    store.enqueue_outbound(kind="lease.minted", chunk_id="ch_2", lease_id="lease_2", payload="{}", created_at=_NOW)

    with TestClient(app) as client:
        resp = client.get("/api/runner")

    assert resp.json()["hub"]["buffer_depth"] == 2


@pytest.mark.component
def test_last_tick_reflects_daemon_liveness(tmp_path: Path) -> None:
    app, store = _app_with_status(tmp_path)
    store.record_daemon_liveness(runner_id="runner-local", alive_at=_NOW)

    with TestClient(app) as client:
        resp = client.get("/api/runner")

    assert resp.json()["last_tick_at"] == _NOW.isoformat()


# --------------------------------------------------------------------------- #
# GET /environments
# --------------------------------------------------------------------------- #


@pytest.mark.component
def test_environments_lists_every_held_binding_across_chunks(tmp_path: Path) -> None:
    app, store = _app_with_status(tmp_path)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_binding(chunk_id="ch_2", environment_id="e2", workdir="/ws/e2", bound_at=_NOW + timedelta(minutes=1))

    with TestClient(app) as client:
        resp = client.get("/api/environments")

    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert items == [
        {"environment_id": "e1", "chunk_id": "ch_1", "held_since": _NOW.isoformat()},
        {"environment_id": "e2", "chunk_id": "ch_2", "held_since": (_NOW + timedelta(minutes=1)).isoformat()},
    ]


@pytest.mark.component
def test_a_released_binding_does_not_appear(tmp_path: Path) -> None:
    app, store = _app_with_status(tmp_path)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_release(chunk_id="ch_1", environment_id="e1", released_at=_NOW + timedelta(minutes=1))

    with TestClient(app) as client:
        resp = client.get("/api/environments")

    assert resp.json()["items"] == []


# --------------------------------------------------------------------------- #
# GET /asks?open=true
# --------------------------------------------------------------------------- #


@pytest.mark.component
def test_open_asks_lists_an_unforwarded_ask(tmp_path: Path) -> None:
    app, store = _app_with_status(tmp_path)
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

    with TestClient(app) as client:
        resp = client.get("/api/asks", params={"open": "true"})

    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0] == {
        "question_id": "qn_1",
        "chunk_id": "ch_1",
        "lease_id": "lease_1",
        "question": "which branch?",
        "options": ["main", "dev"],
        "session_id": "sess-a",
        "asked_at": _NOW.isoformat(),
    }


@pytest.mark.component
def test_open_asks_includes_a_forwarded_and_parked_ask(tmp_path: Path) -> None:
    app, store = _app_with_status(tmp_path)
    _seed_lease(store)
    store.record_ask(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="which branch?",
        options=[],
        session_id="sess-a",
        asked_at=_NOW,
    )
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)

    with TestClient(app) as client:
        resp = client.get("/api/asks", params={"open": "true"})

    assert [item["question_id"] for item in resp.json()["items"]] == ["qn_1"]


@pytest.mark.component
def test_an_answered_ask_does_not_appear(tmp_path: Path) -> None:
    app, store = _app_with_status(tmp_path)
    _seed_lease(store)
    store.record_ask(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="which branch?",
        options=[],
        session_id="sess-a",
        asked_at=_NOW,
    )
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)
    store.record_park_resume(lease_id="lease_1", question_id="qn_1", resumed_at=_NOW + timedelta(minutes=1))

    with TestClient(app) as client:
        resp = client.get("/api/asks", params={"open": "true"})

    assert resp.json()["items"] == []


@pytest.mark.component
def test_open_false_is_refused_rather_than_answered_wrong(tmp_path: Path) -> None:
    app, _store = _app_with_status(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/asks", params={"open": "false"})

    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# GET /escalations
# --------------------------------------------------------------------------- #


@pytest.mark.component
def test_an_escalated_lease_appears_with_its_resume_command(tmp_path: Path) -> None:
    app, store = _app_with_status(tmp_path)
    _seed_lease(store, lease_id="lease_1", chunk_id="ch_1", epoch=1)
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    closed_at = _NOW + timedelta(minutes=5)
    store.record_closure(
        lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="escalated", closed_at=closed_at
    )

    with TestClient(app) as client:
        resp = client.get("/api/escalations")

    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0] == {
        "chunk_id": "ch_1",
        "lease_id": "lease_1",
        "node_id": "nd_build",
        "epoch": 1,
        "closed_at": closed_at.isoformat(),
        "resume_command": "cd /ws/e1 && claude --resume sess-a",
    }


@pytest.mark.component
def test_a_superseded_escalation_does_not_appear(tmp_path: Path) -> None:
    """A later lease mint for the same chunk closes the escalation by supersession."""
    app, store = _app_with_status(tmp_path)
    _seed_lease(store, lease_id="lease_1", chunk_id="ch_1", epoch=1)
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="escalated", closed_at=_NOW)
    _seed_lease(store, lease_id="lease_2", chunk_id="ch_1", epoch=2, created_at=_NOW + timedelta(minutes=1))

    with TestClient(app) as client:
        resp = client.get("/api/escalations")

    assert resp.json()["items"] == []


@pytest.mark.component
def test_a_non_escalated_closure_does_not_appear(tmp_path: Path) -> None:
    app, store = _app_with_status(tmp_path)
    _seed_lease(store)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="failed", closed_at=_NOW)

    with TestClient(app) as client:
        resp = client.get("/api/escalations")

    assert resp.json()["items"] == []


# --------------------------------------------------------------------------- #
# GET /takeovers — the stranded-takeover recovery surface (issue #52)
# --------------------------------------------------------------------------- #


@pytest.mark.component
def test_an_open_takeover_appears_with_its_id_and_held_since(tmp_path: Path) -> None:
    app, store = _app_with_status(tmp_path)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    opened_at = _NOW + timedelta(minutes=5)
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id=None,
        session_id="sess-a",
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=opened_at,
    )

    with TestClient(app) as client:
        resp = client.get("/api/takeovers")

    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0] == {"chunk_id": "ch_1", "takeover_id": "tko_1", "held_since": opened_at.isoformat()}
    assert_all_timestamps_utc(resp.json())


@pytest.mark.component
def test_a_closed_takeover_does_not_appear(tmp_path: Path) -> None:
    app, store = _app_with_status(tmp_path)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id=None,
        session_id="sess-a",
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )
    store.record_takeover_end(takeover_id="tko_1", ended_at=_NOW + timedelta(minutes=1))

    with TestClient(app) as client:
        resp = client.get("/api/takeovers")

    assert resp.json()["items"] == []


@pytest.mark.component
def test_no_open_takeovers_is_an_empty_list(tmp_path: Path) -> None:
    app, _store = _app_with_status(tmp_path)

    with TestClient(app) as client:
        resp = client.get("/api/takeovers")

    assert resp.json()["items"] == []
