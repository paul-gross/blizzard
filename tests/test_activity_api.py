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
from blizzard.hub.store.internal.chunk_store import ChunkStore
from tests.support import build_hub, seed_chunk, seed_graph
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
    store = ChunkStore(hub.engine, hub.clock)
    t0 = hub.clock.now()
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_1", at=t0)
        seed_chunk(conn, "ch_a", graph_id="gr_1", at=t0)  # "minted" at t0
    store.record_promote("ch_a", at=t0 + timedelta(seconds=1))
    store.record_pause("ch_a", paused=True, by="alice", at=t0 + timedelta(seconds=5))

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
    store = ChunkStore(hub.engine, hub.clock)
    t0 = hub.clock.now()
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_1", at=t0)
        seed_chunk(conn, "ch_a", graph_id="gr_1", at=t0)
    store.record_promote("ch_a", at=t0 + timedelta(seconds=1))
    store.record_pause("ch_a", paused=True, by="alice", at=t0 + timedelta(seconds=2))
    store.record_pause("ch_a", paused=False, by="alice", at=t0 + timedelta(seconds=3))

    feed = _activity(hub)
    ats = [row["at"] for row in feed]
    assert ats == sorted(ats, reverse=True)


def test_runner_bearer_token_is_rejected(tmp_path: Path) -> None:
    token = _seed_enrolled(tmp_path)
    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)

    resp = hub.client.get("/api/activity", headers=_bearer(token))
    assert resp.status_code == 403
