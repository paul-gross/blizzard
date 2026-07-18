"""``POST``/``PATCH /chunks/{id}/takeovers`` (issue #52).

Exercised over a real store via TestClient, mirroring
``tests/test_runner_status_api.py``'s convention: the route's shape, its 409/503/404
forms, and the store-derivation it delegates to (:class:`TakeoverService`, pinned at
the domain level by ``tests/test_runner_takeover.py``) are the point here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.foundation.clock import FixedClock
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.takeover import TakeoverService
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.store.repository import NewLease
from tests.runner_fakes import FakeHarness, FakeProbe, make_store

_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)


def _app_with_takeover(tmp_path: Path, *, clock: FixedClock | None = None, probe: FakeProbe | None = None):  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100"), verdict=None
    )
    service = TakeoverService(store, clock or FixedClock(_NOW), harness, probe or FakeProbe())
    return create_app(config, runner_store=store, takeover=service), store


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
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


@pytest.mark.component
def test_503_when_takeover_service_unwired(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    with TestClient(create_app(config)) as client:
        assert client.post("/api/chunks/ch_1/takeovers", json={}).status_code == 503
        assert client.patch("/api/chunks/ch_1/takeovers/tko_1").status_code == 503


@pytest.mark.component
def test_open_over_a_parked_chunk_returns_the_interactive_command(tmp_path: Path) -> None:
    app, store = _app_with_takeover(tmp_path)
    _seed_lease(store)
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)

    with TestClient(app) as client:
        resp = client.post("/api/chunks/ch_1/takeovers", json={})

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["command"] == "cd /ws/e1 && claude --resume sess-a"
    assert body["workdir"] == "/ws/e1"
    assert body["takeover_id"]
    assert store.open_takeover_for_chunk("ch_1") is not None


@pytest.mark.component
def test_open_without_force_over_a_live_worker_is_409(tmp_path: Path) -> None:
    app, store = _app_with_takeover(tmp_path)
    _seed_lease(store)  # active, not parked — a live attempt

    with TestClient(app) as client:
        resp = client.post("/api/chunks/ch_1/takeovers", json={})

    assert resp.status_code == 409, resp.text
    assert store.open_takeover_for_chunk("ch_1") is None


@pytest.mark.component
def test_open_with_force_over_a_live_worker_kills_it_and_fences_the_epoch(tmp_path: Path) -> None:
    probe = FakeProbe(alive={(100, "start-100")})
    app, store = _app_with_takeover(tmp_path, probe=probe)
    _seed_lease(store)

    with TestClient(app) as client:
        resp = client.post("/api/chunks/ch_1/takeovers", json={"force": True})

    assert resp.status_code == 201, resp.text
    assert probe.killed == [100]
    record = store.open_takeover_for_chunk("ch_1")
    assert record is not None
    assert record.fence_epoch == 2


@pytest.mark.component
def test_a_second_open_while_one_is_open_is_409(tmp_path: Path) -> None:
    app, store = _app_with_takeover(tmp_path)
    _seed_lease(store)
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)

    with TestClient(app) as client:
        first = client.post("/api/chunks/ch_1/takeovers", json={})
        assert first.status_code == 201, first.text
        second = client.post("/api/chunks/ch_1/takeovers", json={})

    assert second.status_code == 409


@pytest.mark.component
def test_end_marks_the_takeover_closed(tmp_path: Path) -> None:
    app, store = _app_with_takeover(tmp_path)
    _seed_lease(store)
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)

    with TestClient(app) as client:
        opened = client.post("/api/chunks/ch_1/takeovers", json={}).json()
        end = client.patch(f"/api/chunks/ch_1/takeovers/{opened['takeover_id']}")

    assert end.status_code == 200, end.text
    assert end.json() == {"takeover_id": opened["takeover_id"], "ended": True}
    assert store.open_takeover_for_chunk("ch_1") is None


@pytest.mark.component
def test_end_an_unknown_takeover_is_404(tmp_path: Path) -> None:
    app, _store = _app_with_takeover(tmp_path)

    with TestClient(app) as client:
        resp = client.patch("/api/chunks/ch_1/takeovers/tko_bogus")

    assert resp.status_code == 404
