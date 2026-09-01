"""``GET /api/chunks`` — the bulk-read list path (component tier, blizzard#421).

Proves the route reads the fleet's facts and routes with one bulk query each, so the query
count is unchanged as fleet size grows and never reaches `load_facts`/`route_of` at all."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.work import ChunkFacts
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_store import ChunkStore
from tests.support import build_hub, count_queries, hub_store_connections, ingest

pytestmark = pytest.mark.component


def _seed(hub, n: int) -> None:  # type: ignore[no-untyped-def]
    for i in range(n):
        ingest(hub, [{"source": "default", "ref": str(i)}])


def test_list_chunks_query_count_is_independent_of_fleet_size(tmp_path: Path) -> None:
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small = build_hub(tmp_path / "small")
    _seed(small, 3)
    large = build_hub(tmp_path / "large")
    _seed(large, 9)  # 3x the small fleet

    results: dict[str, int] = {}

    def call(hub, key: str) -> None:  # type: ignore[no-untyped-def]
        resp = hub.client.get("/api/chunks")
        assert resp.status_code == 200, resp.text
        results[key] = len(resp.json())

    small_count = count_queries(small.engine, lambda: call(small, "small"))
    large_count = count_queries(large.engine, lambda: call(large, "large"))

    assert results == {"small": 3, "large": 9}
    assert small_count == large_count


class _CountingChunkStore(ChunkStore):
    """Counts calls to the bulk and per-chunk read seams, so a test can pin which shape
    `list_chunks` actually reaches — mirrors `test_load_all_facts_store`'s own
    `_CountingChunkStore`, extended to routes."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        super().__init__(store, clock)
        self.load_all_facts_calls = 0
        self.load_all_routes_calls = 0
        self.load_facts_calls = 0
        self.route_of_calls = 0

    def load_all_facts(self) -> dict[str, ChunkFacts]:
        self.load_all_facts_calls += 1
        return super().load_all_facts()

    def load_all_routes(self) -> dict[str, Route]:
        self.load_all_routes_calls += 1
        return super().load_all_routes()

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        self.load_facts_calls += 1
        return super().load_facts(chunk_id)

    def route_of(self, chunk_id: str) -> Route | None:
        self.route_of_calls += 1
        return super().route_of(chunk_id)


def test_list_chunks_calls_bulk_reads_and_never_load_facts_or_route_of(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ingest(hub, [{"source": "default", "ref": "1"}])
    ingest(hub, [{"source": "default", "ref": "2"}])

    counting = _CountingChunkStore(hub_store_connections(hub.engine), hub.clock)
    assert hub.app is not None
    hub.app.state.services = replace(hub.services, chunks=replace(hub.services.chunks, facts=counting, route=counting))

    resp = hub.client.get("/api/chunks")

    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 2
    assert counting.load_all_facts_calls == 1
    assert counting.load_all_routes_calls == 1
    assert counting.load_facts_calls == 0
    assert counting.route_of_calls == 0
