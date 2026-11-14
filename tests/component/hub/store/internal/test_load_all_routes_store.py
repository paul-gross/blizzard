"""``IReadChunkRouteRepository.load_all_routes`` (component tier).

Proves the bulk read derives the same route per chunk as ``route_of`` called one at a
time, across ``test_chunk_facts_store_load_all_facts``'s fixture, in a bounded query count."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine

from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from blizzard.hub.domain.runners.route import Route
from tests.support.chunk_units import chunk_unit
from tests.support.load_all_facts_seed import LIVE_CHUNK_IDS, T0, seed_fixture, seeded_chunk_stores
from tests.support.scaffolding import count_queries, hub_store_connections, seed_chunk_record, seed_promote


def test_bulk_read_matches_per_chunk_route_of_across_the_fixture(tmp_path: Path) -> None:
    store, engine, clock = seeded_chunk_stores(tmp_path)
    seed_fixture(store, engine, clock)

    bulk = store.route.load_all_routes()

    for chunk_id in LIVE_CHUNK_IDS:
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
    store, engine, clock = seeded_chunk_stores(tmp_path)
    seed_fixture(store, engine, clock)

    bulk = store.route.load_all_routes()

    assert "ch_running" in bulk  # record_route, never released
    assert "ch_kitchen_sink" not in bulk  # record_route then record_route_released
    assert "ch_not_ready" not in bulk  # never claimed at all


def test_bulk_read_query_count_is_independent_of_fleet_size(tmp_path: Path) -> None:
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small, small_engine, _ = seeded_chunk_stores(tmp_path / "small")
    _seed_route(small, small_engine, "ch_a")

    large, large_engine, _ = seeded_chunk_stores(tmp_path / "large")
    for i in range(40):
        _seed_route(large, large_engine, f"ch_{i}")

    small_count = count_queries(small_engine, small.route.load_all_routes)
    large_count = count_queries(large_engine, large.route.load_all_routes)

    assert small_count == large_count
    assert large_count < 40  # bounded by table count, not chunk count


def _seed_route(store: ChunkStores, engine: Engine, chunk_id: str) -> None:
    seed_chunk_record(store, Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=[], minted_at=T0))
    seed_promote(store, engine, chunk_id, at=T0)
    with chunk_unit(hub_store_connections(engine), [chunk_id]) as tx:
        store.route.record_route(
            Route(chunk_id=chunk_id, runner_id="r1", workspace_id="w1", environment_ids=["e1"], created_at=T0),
            token_hash=f"th_{chunk_id}",
            at=T0,
            transaction=tx,
        )
