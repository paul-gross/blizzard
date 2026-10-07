"""The runner-local active-lease list — ``GET /api/leases``.

Exercised over a real store via TestClient, hub-free. The route's shape, its binding
join, its empty and unwired forms, and the derivation→wire mapping (``parked`` via real
park facts, ``spawning`` via a null pid) are the point.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.foundation.clock import FixedClock
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.leases.activity import LocalLeaseService
from blizzard.runner.leases.model import NewLease
from blizzard.wire.lease import LeaseView
from tests.runner_fakes import FakeProbe, make_store, make_stores
from tests.support import assert_all_timestamps_utc

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _app_with_leases(tmp_path: Path, *, clock: FixedClock | None = None, probe: FakeProbe | None = None):  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    service = LocalLeaseService(
        clock or FixedClock(_NOW),
        probe or FakeProbe(),
        lease_record=store,
        liveness=store,
        asks=store,
        environments=store,
        overload=store,
        elicitations=store,
    )
    return create_app(config, runner_stores=make_stores(store), leases=service), store


def _seed_lease(store, **overrides: object) -> None:  # type: ignore[no-untyped-def]
    fields: dict[str, object] = {
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
    store.record_lease(NewLease(**fields))  # type: ignore[arg-type]


@pytest.mark.component
def test_empty_store_returns_empty_items_not_an_error(tmp_path: Path) -> None:
    app, _store = _app_with_leases(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/leases")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"items": []}


@pytest.mark.component
def test_503_when_store_and_leases_unwired(tmp_path: Path) -> None:
    """The store-free app (OpenAPI export / unit boot) refuses to serve rather than pretend."""
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    with TestClient(create_app(config)) as client:
        resp = client.get("/api/leases")
    assert resp.status_code == 503


@pytest.mark.component
def test_running_lease_shape_and_binding_join(tmp_path: Path) -> None:
    app, store = _app_with_leases(tmp_path, probe=FakeProbe(alive={(100, "start-100")}))
    _seed_lease(store)
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    beat_at = _NOW + timedelta(minutes=1)
    store.record_heartbeat(lease_id="lease_1", beat_at=beat_at)

    with TestClient(app) as client:
        resp = client.get("/api/leases")

    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]
    # Timestamps carry an explicit UTC offset: the store column is UtcDateTime-typed
    # (`bzh:utc-instants`), and `_view` serializes with `iso_utc`.
    assert item == {
        "lease_id": "lease_1",
        "chunk_id": "ch_1",
        "graph_id": "gr_1",
        "node_id": "nd_build",
        "node_name": "build",
        "epoch": 1,
        "session_id": "sess-a",
        "harness_id": "claude_code",
        "pid": 100,
        "environment_id": "e1",
        "workdir": "/ws/e1",
        "created_at": _NOW.isoformat(),
        "last_heartbeat_at": beat_at.isoformat(),
        "state": "running",
        "closed_at": None,
        "closure_reason": None,
        "stale_after_seconds": 3600,
    }


@pytest.mark.component
def test_timestamps_serialize_with_an_explicit_utc_offset(tmp_path: Path) -> None:
    """Timestamps carry an explicit ``+00:00`` offset — asserted on the literal serialized
    strings, not just the round-tripped value."""
    app, store = _app_with_leases(tmp_path, probe=FakeProbe(alive={(100, "start-100")}))
    _seed_lease(store)
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW + timedelta(minutes=1))

    with TestClient(app) as client:
        resp = client.get("/api/leases")

    body = resp.json()
    item = body["items"][0]
    assert item["created_at"] == "2026-07-16T12:00:00+00:00"
    assert item["last_heartbeat_at"] == "2026-07-16T12:01:00+00:00"
    # Every timestamp in the response, via the shared walker.
    assert_all_timestamps_utc(body)

    # Parsing the string recovers the true instant.
    assert datetime.fromisoformat(item["last_heartbeat_at"]) == _NOW + timedelta(minutes=1)


@pytest.mark.component
def test_spawning_state_reaches_the_wire_via_a_null_pid(tmp_path: Path) -> None:
    """Pins the derivation->wire mapping beyond the happy path (watch item #3 sibling)."""
    app, store = _app_with_leases(tmp_path)
    _seed_lease(store)
    # No record_spawn — pid/session_id stay unset, so the lease derives `spawning`.

    with TestClient(app) as client:
        resp = client.get("/api/leases")

    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["state"] == "spawning"
    assert items[0]["pid"] is None
    assert items[0]["session_id"] is None
    assert items[0]["last_heartbeat_at"] is None


@pytest.mark.component
def test_closed_lease_appears_after_active_with_state_and_reason(tmp_path: Path) -> None:
    """The widened route: recently-closed leases join active ones,
    ordered after them, carrying ``state: "closed"`` and the closure reason on the wire."""
    app, store = _app_with_leases(tmp_path, probe=FakeProbe(alive={(100, "start-100")}))
    _seed_lease(store, lease_id="lease_1", chunk_id="ch_1")
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    _seed_lease(store, lease_id="lease_2", chunk_id="ch_2")
    closed_at = _NOW + timedelta(minutes=5)
    store.record_closure(lease_id="lease_2", chunk_id="ch_2", node_id="nd_build", reason="failed", closed_at=closed_at)

    with TestClient(app) as client:
        resp = client.get("/api/leases")

    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [item["lease_id"] for item in items] == ["lease_1", "lease_2"]
    active, closed = items
    assert active["state"] == "running"
    assert active["closed_at"] is None
    assert active["closure_reason"] is None
    assert closed["state"] == "closed"
    assert closed["closed_at"] == closed_at.isoformat()
    assert closed["closure_reason"] == "failed"
    assert closed["stale_after_seconds"] == 3600
    assert_all_timestamps_utc({"items": items})


@pytest.mark.component
def test_parked_state_reaches_the_wire_via_real_park_facts(tmp_path: Path) -> None:
    """Watch item #3: `parked` driven end to end by a real park fact, not a stubbed boolean."""
    app, store = _app_with_leases(tmp_path, probe=FakeProbe(alive={(100, "start-100")}))
    _seed_lease(store)
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="q_1", parked_at=_NOW)

    with TestClient(app) as client:
        resp = client.get("/api/leases")

    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["state"] == "parked"


def _spawn(store) -> None:  # type: ignore[no-untyped-def]
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )


def _seed_active(store) -> None:  # type: ignore[no-untyped-def]
    _spawn(store)


def _seed_parked(store) -> None:  # type: ignore[no-untyped-def]
    _spawn(store)
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="q_1", parked_at=_NOW)


def _seed_spawning(store) -> None:  # type: ignore[no-untyped-def]
    pass


def _seed_closed(store) -> None:  # type: ignore[no-untyped-def]
    _spawn(store)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="failed", closed_at=_NOW)


@pytest.mark.component
@pytest.mark.parametrize(
    ("seed", "now", "state"),
    [
        (_seed_active, _NOW, "running"),
        (_seed_parked, _NOW, "parked"),
        (_seed_spawning, _NOW, "spawning"),
        (_seed_active, _NOW + timedelta(hours=2), "stale"),
        (_seed_closed, _NOW, "closed"),
    ],
)
def test_every_lease_state_serializes_the_same_ordered_keys(tmp_path: Path, seed, now: datetime, state: str) -> None:  # type: ignore[no-untyped-def]
    app, store = _app_with_leases(tmp_path, clock=FixedClock(now), probe=FakeProbe(alive={(100, "start-100")}))
    _seed_lease(store)
    seed(store)

    with TestClient(app) as client:
        resp = client.get("/api/leases")

    assert resp.status_code == 200, resp.text
    (item,) = resp.json()["items"]
    assert item["state"] == state
    assert list(item) == list(LeaseView.model_fields)
