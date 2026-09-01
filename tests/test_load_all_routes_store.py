"""``IReadChunkRouteRepository.load_all_routes`` (component tier, blizzard#421).

Proves the bulk read derives the same route per chunk as ``route_of`` called one at a
time, across ``test_load_all_facts_store``'s fixture, in a bounded query count."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.domain.chunks.stores import ChunkStores
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.work import Chunk
from tests.support import count_queries
from tests.test_load_all_facts_store import _LIVE_CHUNK_IDS, _T0, _seed_fixture, _store

pytestmark = pytest.mark.component


def test_bulk_read_matches_per_chunk_route_of_across_the_fixture(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _seed_fixture(store, engine)

    bulk = store.route.load_all_routes()

    for chunk_id in _LIVE_CHUNK_IDS:
        expected = store.route.route_of(chunk_id)
        if expected is None:
            assert chunk_id not in bulk, chunk_id
            continue
        got = bulk[chunk_id]
        assert got.chunk_id == expected.chunk_id
        assert got.runner_id == expected.runner_id
        assert got.workspace_id == expected.workspace_id
        assert got.environment_ids == expected.environment_ids
        assert got.created_at == expected.created_at
        assert got.route_id == expected.route_id


def test_bulk_read_includes_the_live_route_and_excludes_the_released_one(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _seed_fixture(store, engine)

    bulk = store.route.load_all_routes()

    assert "ch_running" in bulk  # record_route, never released
    assert "ch_kitchen_sink" not in bulk  # record_route then record_route_released
    assert "ch_not_ready" not in bulk  # never claimed at all


def test_bulk_read_query_count_is_independent_of_fleet_size(tmp_path: Path) -> None:
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small, small_engine = _store(tmp_path / "small")
    _seed_route(small, "ch_a")

    large, large_engine = _store(tmp_path / "large")
    for i in range(40):
        _seed_route(large, f"ch_{i}")

    small_count = count_queries(small_engine, small.route.load_all_routes)
    large_count = count_queries(large_engine, large.route.load_all_routes)

    assert small_count == large_count
    assert large_count < 40  # bounded by table count, not chunk count


def _seed_route(store: ChunkStores, chunk_id: str) -> None:
    store.record.mint(Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=[], minted_at=_T0))
    store.queue.record_promote(chunk_id, at=_T0)
    store.route.record_route(
        Route(chunk_id=chunk_id, runner_id="r1", workspace_id="w1", environment_ids=["e1"], created_at=_T0),
        token_hash=f"th_{chunk_id}",
        at=_T0,
    )
