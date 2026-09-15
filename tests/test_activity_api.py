"""``GET /api/activity`` — the board's Event log backfill on page load (issue #213,
Phase 3, component tier).

Proves the route's own contract off a real, migrated hub app: the default 24h/200
window, ``since``/``limit`` handling, newest-first ordering, and the same auth gating
``GET /api/events`` carries, including a runner's bearer token rejection."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.config import RUNNER_AUTH_ENFORCE
from blizzard.hub.store.internal.chunk_rows import record_deleted_row
from tests.support import build_hub, chunk_stores, seed_chunk, seed_graph
from tests.test_fleet_auth import _bearer, _seed_enrolled

pytestmark = pytest.mark.component


def _activity(hub, **params) -> list[dict]:  # type: ignore[no-untyped-def]
    resp = hub.client.get("/api/activity", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["activity"]


def test_default_window_is_24h(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    t0 = hub.clock.now()
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_1", at=t0 - timedelta(hours=30))
        seed_chunk(conn, "ch_old", graph_id="gr_1", at=t0 - timedelta(hours=25))
        seed_chunk(conn, "ch_new", graph_id="gr_1", at=t0 - timedelta(hours=1))

    chunk_ids = {row["chunk_id"] for row in _activity(hub)}
    assert "ch_new" in chunk_ids
    assert "ch_old" not in chunk_ids


def test_default_limit_is_200(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    t0 = hub.clock.now()
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_1", at=t0)
        for i in range(210):
            seed_chunk(conn, f"ch_{i}", graph_id="gr_1", at=t0 + timedelta(seconds=i))

    assert len(_activity(hub)) == 200


def test_explicit_since_narrows(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    store = chunk_stores(hub.engine, hub.clock)
    t0 = hub.clock.now()
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_1", at=t0)
        seed_chunk(conn, "ch_a", graph_id="gr_1", at=t0)  # "minted" at t0
    store.queue.record_promote("ch_a", at=t0 + timedelta(seconds=1))
    store.lifecycle.record_pause("ch_a", paused=True, by="alice", at=t0 + timedelta(seconds=5))

    narrow = _activity(hub, since=iso_utc(t0 + timedelta(seconds=3)))
    causes = {row["cause"] for row in narrow if row["type"] == "chunk-changed"}
    assert causes == {"paused"}


def test_limit_out_of_bounds_422s(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.get("/api/activity", params={"limit": 0}).status_code == 422
    assert hub.client.get("/api/activity", params={"limit": 1001}).status_code == 422


def test_naive_since_is_coerced_not_raised(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.get("/api/activity", params={"since": "2020-01-01T00:00:00"})
    assert resp.status_code == 200


def test_rows_come_back_newest_first(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    store = chunk_stores(hub.engine, hub.clock)
    t0 = hub.clock.now()
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_1", at=t0)
        seed_chunk(conn, "ch_a", graph_id="gr_1", at=t0)
    store.queue.record_promote("ch_a", at=t0 + timedelta(seconds=1))
    store.lifecycle.record_pause("ch_a", paused=True, by="alice", at=t0 + timedelta(seconds=2))
    store.lifecycle.record_pause("ch_a", paused=False, by="alice", at=t0 + timedelta(seconds=3))

    feed = _activity(hub)
    ats = [row["at"] for row in feed]
    assert ats == sorted(ats, reverse=True)


def test_events_are_capped_by_recency_not_severity(tmp_path: Path) -> None:
    """The feed's event source is pure-recency, not the severity-ranked read
    ``/api/events`` uses — with more in-window rows than ``limit``, the newest survive
    even when an older row outranks them by severity."""
    hub = build_hub(tmp_path)
    store = chunk_stores(hub.engine, hub.clock)
    t0 = hub.clock.now()
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_1", at=t0)
    oldest_critical_id = store.events.record_event(
        severity="critical",
        kind="worker-lost",
        runner_id="runner-a",
        chunk_id=None,
        lease_id=None,
        node_name=None,
        message="oldest, most severe",
        detail=None,
        at=t0,
    )
    newer_info_id_1 = store.events.record_event(
        severity="info",
        kind="work-item-closed",
        runner_id=None,
        chunk_id=None,
        lease_id=None,
        node_name=None,
        message="newer, less severe, first",
        detail=None,
        at=t0 + timedelta(seconds=1),
    )
    newer_info_id_2 = store.events.record_event(
        severity="info",
        kind="work-item-closed",
        runner_id=None,
        chunk_id=None,
        lease_id=None,
        node_name=None,
        message="newer, less severe, second",
        detail=None,
        at=t0 + timedelta(seconds=2),
    )

    rows = _activity(hub, limit=2)
    event_keys = {r["key"] for r in rows if r["type"] == "event-logged"}
    assert event_keys == {f"event_log:{newer_info_id_1}", f"event_log:{newer_info_id_2}"}
    assert f"event_log:{oldest_critical_id}" not in event_keys


def test_deleted_chunks_events_are_excluded_but_runner_scoped_events_survive(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    store = chunk_stores(hub.engine, hub.clock)
    t0 = hub.clock.now()
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_1", at=t0)
        seed_chunk(conn, "ch_deleted", graph_id="gr_1", at=t0)
    deleted_chunk_event_id = store.events.record_event(
        severity="critical",
        kind="worker-lost",
        runner_id="runner-a",
        chunk_id="ch_deleted",
        lease_id=None,
        node_name=None,
        message="deleted chunk's own event",
        detail=None,
        at=t0 + timedelta(seconds=1),
    )
    runner_scoped_event_id = store.events.record_event(
        severity="info",
        kind="work-item-closed",
        runner_id="runner-a",
        chunk_id=None,
        lease_id=None,
        node_name=None,
        message="runner-scoped event",
        detail=None,
        at=t0 + timedelta(seconds=2),
    )
    with hub.engine.begin() as conn:
        record_deleted_row(conn, "ch_deleted", by="alice", at=t0 + timedelta(seconds=3))

    rows = _activity(hub)
    event_keys = {r["key"] for r in rows if r["type"] == "event-logged"}
    assert f"event_log:{deleted_chunk_event_id}" not in event_keys
    assert f"event_log:{runner_scoped_event_id}" in event_keys
    deletion_rows = [r for r in rows if r["type"] == "chunk-changed" and r["cause"] == "deleted"]
    assert len(deletion_rows) == 1
    assert deletion_rows[0]["chunk_id"] == "ch_deleted"


def test_runner_bearer_token_is_rejected(tmp_path: Path) -> None:
    token = _seed_enrolled(tmp_path)
    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)

    resp = hub.client.get("/api/activity", headers=_bearer(token))
    assert resp.status_code == 403
