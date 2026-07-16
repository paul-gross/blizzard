"""The runner-local active-lease list — ``GET /api/leases`` (issue #28).

Exercised over a real store via TestClient, mirroring ``tests/test_workspace_prompt_api.py``'s
convention. Hub-free: nothing here reaches for the hub or the forge — the route's shape,
its binding join, its empty and unwired forms, and the derivation→wire mapping (``parked``
via real park facts, ``spawning`` via a null pid) are the point.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.foundation.clock import FixedClock
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.leases import LocalLeaseService
from blizzard.runner.store.repository import NewLease
from tests.runner_fakes import FakeProbe, make_store
from tests.support import assert_all_timestamps_utc

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _app_with_leases(tmp_path: Path, *, clock: FixedClock | None = None, probe: FakeProbe | None = None):  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    service = LocalLeaseService(store, clock or FixedClock(_NOW), probe or FakeProbe())
    return create_app(config, runner_store=store, leases=service), store


def _seed_lease(store, **overrides: object) -> None:  # type: ignore[no-untyped-def]
    fields: dict[str, object] = {
        "lease_id": "lease_1",
        "chunk_id": "ch_1",
        "graph_id": "gr_1",
        "node_id": "nd_build",
        "node_name": "build",
        "epoch": 1,
        "runner_id": "r1",
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
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
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
    # (issue #28, `bzh:utc-instants`), and `_view` serializes with `iso_utc`.
    assert item == {
        "lease_id": "lease_1",
        "chunk_id": "ch_1",
        "graph_id": "gr_1",
        "node_id": "nd_build",
        "node_name": "build",
        "epoch": 1,
        "session_id": "sess-a",
        "pid": 100,
        "environment_id": "e1",
        "workdir": "/ws/e1",
        "created_at": _NOW.isoformat(),
        "last_heartbeat_at": beat_at.isoformat(),
        "state": "running",
    }


@pytest.mark.component
def test_timestamps_serialize_with_an_explicit_utc_offset(tmp_path: Path) -> None:
    """The panel derives heartbeat age client-side; a naive string would skew it silently.

    Phase 6 renders age as ``Date.now() - new Date(last_heartbeat_at)``. JavaScript reads
    an ISO string **without** an offset as *local* time, so on any non-UTC machine every
    age is wrong by the reader's UTC offset — a fresh beat reads hours old (UTC+) or
    pins to zero (UTC-). Nothing on the backend would notice: the value round-trips
    through Python fine. So pin the **literal serialized bytes**, not the parsed value.
    """
    app, store = _app_with_leases(tmp_path, probe=FakeProbe(alive={(100, "start-100")}))
    _seed_lease(store)
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW + timedelta(minutes=1))

    with TestClient(app) as client:
        resp = client.get("/api/leases")

    body = resp.json()
    item = body["items"][0]
    assert item["created_at"] == "2026-07-16T12:00:00+00:00"
    assert item["last_heartbeat_at"] == "2026-07-16T12:01:00+00:00"
    # The unambiguous-designator property itself, stated directly, via the shared
    # walker (``tests/support.py``) rather than an ad hoc field-by-field loop — so a
    # later route addition is covered without touching this test.
    assert_all_timestamps_utc(body)

    # And the property that actually matters: a JS-equivalent parse recovers the true
    # instant, not one shifted by the reader's offset.
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
def test_parked_state_reaches_the_wire_via_real_park_facts(tmp_path: Path) -> None:
    """Watch item #3: `parked` driven end to end by a real park fact, not a stubbed boolean."""
    app, store = _app_with_leases(tmp_path, probe=FakeProbe(alive={(100, "start-100")}))
    _seed_lease(store)
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="q_1", parked_at=_NOW)

    with TestClient(app) as client:
        resp = client.get("/api/leases")

    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["state"] == "parked"
