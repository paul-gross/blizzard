"""``GET /api/chunks`` — the bulk-read list path (component tier, blizzard#421).

Proves the route reads the fleet's facts and routes with one bulk query each, so the query
count is unchanged as fleet size grows and never reaches `load_facts`/`route_of` at all."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.work import ChunkFacts, WorkRef
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore
from blizzard.hub.store.internal.chunk_route_store import ChunkRouteStore
from blizzard.hub.store.internal.chunk_work_refs_store import ChunkWorkRefsStore
from blizzard.hub.store.internal.graph_store import GraphStore
from tests.support import build_hub, count_queries, hub_store_connections, ingest, seed_chunk, seed_graph

_T0 = datetime(2026, 1, 1, tzinfo=UTC)

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


class _CountingFactsStore(ChunkFactsStore):
    """Counts calls to the bulk and per-chunk facts reads, so a test can pin which shape
    `list_chunks` actually reaches — mirrors `test_load_all_facts_store`'s own
    `_CountingFactsStore`."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        super().__init__(store, clock)
        self.load_all_facts_calls = 0
        self.load_facts_calls = 0

    def load_all_facts(self) -> dict[str, ChunkFacts]:
        self.load_all_facts_calls += 1
        return super().load_all_facts()

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        self.load_facts_calls += 1
        return super().load_facts(chunk_id)


class _CountingRouteStore(ChunkRouteStore):
    """Counts calls to the bulk and per-chunk route reads, the route-seam counterpart
    to `_CountingFactsStore`."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        super().__init__(store, clock)
        self.load_all_routes_calls = 0
        self.route_of_calls = 0

    def load_all_routes(self) -> dict[str, Route]:
        self.load_all_routes_calls += 1
        return super().load_all_routes()

    def route_of(self, chunk_id: str) -> Route | None:
        self.route_of_calls += 1
        return super().route_of(chunk_id)


def test_list_chunks_calls_bulk_reads_and_never_load_facts_or_route_of(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ingest(hub, [{"source": "default", "ref": "1"}])
    ingest(hub, [{"source": "default", "ref": "2"}])

    counting_facts = _CountingFactsStore(hub_store_connections(hub.engine), hub.clock)
    counting_route = _CountingRouteStore(hub_store_connections(hub.engine), hub.clock)
    assert hub.app is not None
    hub.app.state.services = replace(
        hub.services, chunks=replace(hub.services.chunks, facts=counting_facts, route=counting_route)
    )

    resp = hub.client.get("/api/chunks")

    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 2
    assert counting_facts.load_all_facts_calls == 1
    assert counting_route.load_all_routes_calls == 1
    assert counting_facts.load_facts_calls == 0
    assert counting_route.route_of_calls == 0


class _CountingWorkRefsStore(ChunkWorkRefsStore):
    """Counts calls to the pointer-liveness reads, so a test can pin that a fan-out
    read never reaches the per-pointer `find_live_holder` (issue #421)."""

    def __init__(self, store: HubStoreConnections, clock: IClock, *, facts: ChunkFactsStore) -> None:
        super().__init__(store, clock, facts=facts)
        self.find_live_holder_calls = 0
        self.live_holders_calls = 0

    def find_live_holder(self, pointer: WorkRef) -> str | None:
        self.find_live_holder_calls += 1
        return super().find_live_holder(pointer)

    def live_holders(self, pointers):  # type: ignore[no-untyped-def]
        self.live_holders_calls += 1
        return super().live_holders(pointers)


def test_list_chunks_renders_work_refs_with_no_fact_load_or_live_holders_call(tmp_path: Path) -> None:
    """`list_chunks` derives its live-holder map from the chunks and statuses it
    already loaded, with no second read to resolve a pointer's URL."""
    hub = build_hub(tmp_path)
    ingest(hub, [{"source": "default", "ref": "1"}])
    created = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()

    counting_facts = _CountingFactsStore(hub_store_connections(hub.engine), hub.clock)
    counting_work_refs = _CountingWorkRefsStore(hub_store_connections(hub.engine), hub.clock, facts=counting_facts)
    assert hub.app is not None
    hub.app.state.services = replace(
        hub.services, chunks=replace(hub.services.chunks, facts=counting_facts, work_refs=counting_work_refs)
    )

    resp = hub.client.get("/api/chunks")

    assert resp.status_code == 200, resp.text
    by_source_ref = {(row["work_refs"][0]["source"], row["work_refs"][0]["ref"]): row for row in resp.json()}
    assert by_source_ref[("default", "1")]["work_refs"][0]["web_url"] == "http://forge.local/acme/widget/issues/1"
    assert by_source_ref[("hub", created["ref"])]["work_refs"][0]["web_url"] == f"/board/chunk/{created['chunk_id']}"
    assert counting_work_refs.find_live_holder_calls == 0
    assert counting_work_refs.live_holders_calls == 0
    assert counting_facts.load_facts_calls == 0


class _CountingGraphStore(GraphStore):
    """Counts calls to the fully-reifying `get`, so a test can pin that `list_chunks`
    never reaches it (issue #421)."""

    def __init__(self, store: HubStoreConnections) -> None:
        super().__init__(store)
        self.get_calls = 0

    def get(self, graph_id: str) -> Graph | None:
        self.get_calls += 1
        return super().get(graph_id)


def test_list_chunks_never_calls_graphs_get(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ingest(hub, [{"source": "default", "ref": "1"}])
    ingest(hub, [{"source": "default", "ref": "2"}])

    counting_graphs = _CountingGraphStore(hub_store_connections(hub.engine))
    assert hub.app is not None
    hub.app.state.services = replace(hub.services, graphs=counting_graphs)

    resp = hub.client.get("/api/chunks")

    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 2
    assert counting_graphs.get_calls == 0


def _seed_chunks_across_graphs(hub, n_chunks: int, n_graphs: int) -> None:  # type: ignore[no-untyped-def]
    engine = hub.engine
    with engine.begin() as conn:
        for g in range(n_graphs):
            seed_graph(conn, f"gr_{g}", at=_T0)
        for i in range(n_chunks):
            seed_chunk(conn, f"ch_seed_{i}", graph_id=f"gr_{i % n_graphs}", at=_T0)


def test_list_chunks_query_count_is_independent_of_distinct_graph_pin_count(tmp_path: Path) -> None:
    """Extends the fleet-size test to a second axis: the number of *distinct* graphs a
    fleet's chunks pin to must not grow the statement count either."""
    (tmp_path / "few_graphs").mkdir()
    (tmp_path / "many_graphs").mkdir()
    few = build_hub(tmp_path / "few_graphs")
    many = build_hub(tmp_path / "many_graphs")
    _seed_chunks_across_graphs(few, n_chunks=6, n_graphs=2)
    _seed_chunks_across_graphs(many, n_chunks=6, n_graphs=6)

    def call(hub) -> int:  # type: ignore[no-untyped-def]
        resp = hub.client.get("/api/chunks")
        assert resp.status_code == 200, resp.text
        return len(resp.json())

    few_count = count_queries(few.engine, lambda: call(few))
    many_count = count_queries(many.engine, lambda: call(many))

    assert few_count == many_count
