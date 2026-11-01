"""``GET /api/chunks`` — the bulk-read list path (component tier).

Proves the route reads only its page's facts, routes, edges, and live holders with one
batched query each, so the query count is unchanged as fleet size grows and the route never
reaches a whole-tenant read or `load_facts`/`route_of`."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunk.model import ChunkFacts, WorkRef
from blizzard.hub.domain.config.changes import RecordKind
from blizzard.hub.domain.delivery.flows import FlowResource
from blizzard.hub.domain.graph.model import Graph
from blizzard.hub.domain.kernel.pagination import MAX_LIMIT
from blizzard.hub.domain.runners.route import Route
from blizzard.hub.live_config import ConfigObjectCache
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore
from blizzard.hub.store.internal.chunk_route_store import ChunkRouteStore
from blizzard.hub.store.internal.chunk_work_refs_store import ChunkWorkRefsStore
from blizzard.hub.store.internal.config_revisions_store import ConfigRevisionsStore
from blizzard.hub.store.internal.flow_store import FlowStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.work_sources.registry import BuiltWorkSource, StoreWorkSourceRegistry
from tests.support import (
    FakeWorkSource,
    HubHarness,
    build_hub,
    count_queries,
    count_rows_read,
    hub_store_connections,
    ingest,
    make_ready,
    report_lease,
    seed_chunk,
    seed_graph,
    serve_default_tenant,
)
from tests.test_ingest_and_queue import BUILD_REVIEW_DELIVER_YAML, pass_node

_T0 = datetime(2026, 1, 1, tzinfo=UTC)

pytestmark = pytest.mark.component


def _seed(hub: HubHarness, n: int) -> None:
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

    def call(hub: HubHarness, key: str) -> None:
        resp = hub.client.get("/api/chunks")
        assert resp.status_code == 200, resp.text
        results[key] = len(resp.json()["chunks"])

    small_count = count_queries(small.engine, lambda: call(small, "small"))
    large_count = count_queries(large.engine, lambda: call(large, "large"))

    assert results == {"small": 3, "large": 9}
    assert small_count == large_count


def _seed_in_line(hub: HubHarness, n: int) -> None:
    """``n`` chunks, each submitted to the same two flows."""
    _seed(hub, n)
    flows = FlowStore(hub_store_connections(hub.engine), hub.clock)
    for row in hub.client.get("/api/chunks").json()["chunks"]:
        flows.submit(
            row["chunk_id"],
            node_id="nd_deliver",
            epoch=1,
            generation=0,
            resources=[FlowResource("repo_a", "main"), FlowResource("repo_b", "main")],
            at=_T0,
        )


def test_list_chunks_query_count_is_independent_of_fleet_size_with_its_chunks_in_line(tmp_path: Path) -> None:
    """Each chunk's place reads the whole line of the flows the page holds: the statement count stays flat however
    many chunks stand in it."""
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small = build_hub(tmp_path / "small")
    _seed_in_line(small, 3)
    large = build_hub(tmp_path / "large")
    _seed_in_line(large, 9)

    def call(hub: HubHarness) -> None:
        resp = hub.client.get("/api/chunks")
        assert resp.status_code == 200, resp.text
        assert all(len(row["flow_places"]) == 2 for row in resp.json()["chunks"])

    assert count_queries(small.engine, lambda: call(small)) == count_queries(large.engine, lambda: call(large))


def test_list_resolves_store_backed_sources_once_per_name_and_only_on_the_page(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource(), "other": FakeWorkSource(name="other")})
    # Ingest with the fixture registry, then swap in the production registry and cache.
    for i in range(5):
        ingest(hub, [{"source": "default", "ref": str(i)}, {"source": "other", "ref": str(i)}])
    scoped = hub_store_connections(hub.engine)

    class CountingRevisions(ConfigRevisionsStore):
        def __init__(self, store: ScopedConnections) -> None:
            super().__init__(store)
            self.calls: list[str] = []

        def revisions(self, kind: RecordKind, key: str):
            self.calls.append(key)
            return super().revisions(kind, key)

    revisions = CountingRevisions(scoped)
    registry = StoreWorkSourceRegistry(
        records=hub.services.work_source_records,
        objects=ConfigObjectCache(revisions),
        build=lambda record: BuiltWorkSource(record, FakeWorkSource(name=record.name), lambda: None),
        built_in=hub.work_sources.get("hub"),  # type: ignore[arg-type]
    )
    serve_default_tenant(hub, replace(hub.services, work_sources=registry))

    def page() -> None:
        resp = hub.client.get("/api/chunks", params={"limit": 2})
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["chunks"]) == 2
        for row in resp.json()["chunks"]:
            assert [(p["label"], p["web_url"]) for p in row["work_refs"]] == [
                (
                    f"default#{row['work_refs'][0]['ref']}",
                    f"http://forge.local/acme/widget/issues/{row['work_refs'][0]['ref']}",
                ),
                (
                    f"other#{row['work_refs'][1]['ref']}",
                    f"http://forge.local/acme/widget/issues/{row['work_refs'][1]['ref']}",
                ),
            ]

    page()  # Warm graph and source adapters before measuring either population.
    assert revisions.calls == ["default", "other"]
    before = count_queries(hub.engine, page)
    rows_before = count_rows_read(hub.engine, page)
    assert revisions.calls[-2:] == ["default", "other"]
    graph_id = hub.client.get("/api/chunks", params={"limit": 1}).json()["chunks"][0]["graph_id"]
    for i in range(5, 11):
        # Off-page rows are older than the two page entries.
        with hub.engine.begin() as conn:
            seed_chunk(conn, f"ch_off_{i}", graph_id=graph_id, at=_T0)
    assert count_queries(hub.engine, page) == before
    assert count_rows_read(hub.engine, page) == rows_before
    assert revisions.calls[-2:] == ["default", "other"]


def test_on_page_fallback_pointers_do_not_repeat_record_reads(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"default": FakeWorkSource()})
    for n in range(9):
        ingest(hub, [{"source": "default", "ref": str(n)}])

    class FallbackSource(FakeWorkSource):
        def label(self, pointer: WorkRef) -> None:
            return None

    class CountingRevisions(ConfigRevisionsStore):
        def __init__(self, store: ScopedConnections) -> None:
            super().__init__(store)
            self.calls: list[str] = []

        def revisions(self, kind: RecordKind, key: str):
            self.calls.append(key)
            return super().revisions(kind, key)

    revisions = CountingRevisions(hub_store_connections(hub.engine))
    registry = StoreWorkSourceRegistry(
        records=hub.services.work_source_records,
        objects=ConfigObjectCache(revisions),
        build=lambda record: BuiltWorkSource(record, FallbackSource(name=record.slug), lambda: None),
        built_in=hub.work_sources.get("hub"),  # type: ignore[arg-type]
    )
    serve_default_tenant(hub, replace(hub.services, work_sources=registry))

    def page(limit: int) -> None:
        response = hub.client.get("/api/chunks", params={"limit": limit})
        assert response.status_code == 200, response.text
        assert len(response.json()["chunks"]) == limit
        for row in response.json()["chunks"]:
            for pointer in row["work_refs"]:
                assert pointer["label"] == f"default#{pointer['ref']}"
                assert pointer["web_url"] == f"http://forge.local/acme/widget/issues/{pointer['ref']}"

    page(3)  # warm the adapter before counting
    revisions.calls.clear()
    small = count_queries(hub.engine, lambda: page(3))
    assert revisions.calls == ["default"]
    revisions.calls.clear()
    large = count_queries(hub.engine, lambda: page(9))
    assert revisions.calls == ["default"]
    assert small == large


class _CountingFactsStore(ChunkFactsStore):
    """Counts calls to the bulk and per-chunk facts reads, so a test can pin which shape
    `list_chunks` actually reaches — mirrors `test_load_all_facts_store`'s own
    `_CountingFactsStore`."""

    def __init__(self, store: ScopedConnections, clock: IClock) -> None:
        super().__init__(store, clock)
        self.load_all_facts_calls = 0
        self.load_facts_for_calls = 0
        self.load_facts_calls = 0

    def load_all_facts(self) -> dict[str, ChunkFacts]:
        self.load_all_facts_calls += 1
        return super().load_all_facts()

    def load_facts_for(self, chunk_ids):
        self.load_facts_for_calls += 1
        return super().load_facts_for(chunk_ids)

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        self.load_facts_calls += 1
        return super().load_facts(chunk_id)


class _CountingRouteStore(ChunkRouteStore):
    """Counts calls to the bulk and per-chunk route reads, the route-seam counterpart
    to `_CountingFactsStore`."""

    def __init__(self, store: ScopedConnections, clock: IClock) -> None:
        super().__init__(store, clock)
        self.load_all_routes_calls = 0
        self.routes_for_calls = 0
        self.route_of_calls = 0

    def load_all_routes(self) -> dict[str, Route]:
        self.load_all_routes_calls += 1
        return super().load_all_routes()

    def routes_for(self, chunk_ids):
        self.routes_for_calls += 1
        return super().routes_for(chunk_ids)

    def route_of(self, chunk_id: str) -> Route | None:
        self.route_of_calls += 1
        return super().route_of(chunk_id)


def test_list_chunks_reads_its_page_in_batches_and_never_the_whole_tenant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ingest(hub, [{"source": "default", "ref": "1"}])
    ingest(hub, [{"source": "default", "ref": "2"}])

    counting_facts = _CountingFactsStore(hub_store_connections(hub.engine), hub.clock)
    counting_route = _CountingRouteStore(hub_store_connections(hub.engine), hub.clock)
    assert hub.app is not None
    serve_default_tenant(
        hub, replace(hub.services, chunks=replace(hub.services.chunks, facts=counting_facts, route=counting_route))
    )

    resp = hub.client.get("/api/chunks")

    assert resp.status_code == 200, resp.text
    assert len(resp.json()["chunks"]) == 2
    assert counting_facts.load_all_facts_calls == 0
    assert counting_route.load_all_routes_calls == 0
    assert counting_facts.load_facts_for_calls == 1
    assert counting_route.routes_for_calls == 1
    assert counting_facts.load_facts_calls == 0
    assert counting_route.route_of_calls == 0


class _CountingWorkRefsStore(ChunkWorkRefsStore):
    """Counts calls to the pointer-liveness reads, so a test can pin that a fan-out
    read never reaches the per-pointer `find_live_holder`."""

    def __init__(self, store: ScopedConnections, clock: IClock, *, facts: ChunkFactsStore) -> None:
        super().__init__(store, clock, facts=facts)
        self.find_live_holder_calls = 0
        self.live_holders_calls = 0

    def find_live_holder(self, pointer: WorkRef) -> str | None:
        self.find_live_holder_calls += 1
        return super().find_live_holder(pointer)

    def live_holders(self, pointers):
        self.live_holders_calls += 1
        return super().live_holders(pointers)


def test_list_chunks_renders_work_refs_with_one_batched_live_holders_call(tmp_path: Path) -> None:
    """`list_chunks` resolves its live-holder map with one batched `live_holders` call over the
    page's pointers, never the per-pointer `find_live_holder` or a per-chunk fact load."""
    hub = build_hub(tmp_path)
    ingest(hub, [{"source": "default", "ref": "1"}])
    created = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()

    counting_facts = _CountingFactsStore(hub_store_connections(hub.engine), hub.clock)
    counting_work_refs = _CountingWorkRefsStore(hub_store_connections(hub.engine), hub.clock, facts=counting_facts)
    assert hub.app is not None
    serve_default_tenant(
        hub,
        replace(hub.services, chunks=replace(hub.services.chunks, facts=counting_facts, work_refs=counting_work_refs)),
    )

    resp = hub.client.get("/api/chunks")

    assert resp.status_code == 200, resp.text
    by_source_ref = {(row["work_refs"][0]["source"], row["work_refs"][0]["ref"]): row for row in resp.json()["chunks"]}
    assert by_source_ref[("default", "1")]["work_refs"][0]["web_url"] == "http://forge.local/acme/widget/issues/1"
    assert by_source_ref[("hub", created["ref"])]["work_refs"][0]["web_url"] == f"/board/chunk/{created['chunk_id']}"
    assert counting_work_refs.find_live_holder_calls == 0
    assert counting_work_refs.live_holders_calls == 1
    assert counting_facts.load_facts_calls == 0


class _CountingGraphStore(GraphStore):
    """Counts calls to the fully-reifying `get`, so a test can pin that `list_chunks`
    never reaches it."""

    def __init__(self, store: ScopedConnections) -> None:
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
    serve_default_tenant(hub, replace(hub.services, graphs=counting_graphs))

    resp = hub.client.get("/api/chunks")

    assert resp.status_code == 200, resp.text
    assert len(resp.json()["chunks"]) == 2
    assert counting_graphs.get_calls == 0


def _seed_chunks_across_graphs(hub: HubHarness, n_chunks: int, n_graphs: int) -> None:
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

    def call(hub: HubHarness) -> int:
        resp = hub.client.get("/api/chunks")
        assert resp.status_code == 200, resp.text
        return len(resp.json()["chunks"])

    few_count = count_queries(few.engine, lambda: call(few))
    many_count = count_queries(many.engine, lambda: call(many))

    assert few_count == many_count


# Keyset pagination.


def _delete_chunk(hub: HubHarness, chunk_id: str) -> None:
    """``DELETE /api/chunks/{id}`` — ``httpx2``'s own ``delete()`` refuses ``json``, so
    this goes through ``request`` instead (mirrors ``tests/test_chunk_delete_route.py``'s own)."""
    resp = hub.client.request("DELETE", f"/api/chunks/{chunk_id}", json={})
    assert resp.status_code == 202, resp.text


def _all_pages(hub: HubHarness, *, limit: int) -> list[dict[str, Any]]:
    """Walks ``GET /api/chunks`` at a fixed ``limit``, following ``next_cursor`` until
    it comes back null, and concatenates every page's own chunks in order."""
    chunks: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params: dict[str, str | int] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        resp = hub.client.get("/api/chunks", params=params)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["chunks"]) <= limit
        chunks.extend(body["chunks"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    return chunks


def test_sort_key_ties_paged_concatenation_matches_the_full_unpaginated_order(tmp_path: Path) -> None:
    """`chunk_id desc` makes the sort total when `minted_at` ties;
    paging at `limit=1` must reproduce a single large-limit read's order exactly."""
    hub = build_hub(tmp_path)
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_ties", at=_T0)
        seed_chunk(conn, "ch_b", graph_id="gr_ties", at=_T0)
        seed_chunk(conn, "ch_a", graph_id="gr_ties", at=_T0)  # same minted_at as ch_b — a tie
        seed_chunk(conn, "ch_d", graph_id="gr_ties", at=_T0 + timedelta(seconds=1))
        seed_chunk(conn, "ch_c", graph_id="gr_ties", at=_T0 + timedelta(seconds=1))  # ties with ch_d
        seed_chunk(conn, "ch_e", graph_id="gr_ties", at=_T0 + timedelta(seconds=2))

    full = hub.client.get("/api/chunks", params={"limit": MAX_LIMIT})
    assert full.status_code == 200, full.text
    full_ids = [c["chunk_id"] for c in full.json()["chunks"]]
    assert len(full_ids) == 5

    paged_ids = [c["chunk_id"] for c in _all_pages(hub, limit=1)]

    assert paged_ids == full_ids
    assert len(set(paged_ids)) == len(paged_ids)


def test_ephemeral_only_window_still_pages_every_visible_chunk_exactly_once(tmp_path: Path) -> None:
    """A window landing entirely on grouped-away/deleted rows must retry with a doubled
    window rather than short-paging: three deleted chunks sit ahead of
    three live ones that a `limit=2` page must still surface."""
    hub = build_hub(tmp_path)
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_window", at=_T0)
        seed_chunk(conn, "ch_eph_1", graph_id="gr_window", at=_T0 + timedelta(seconds=6))
        seed_chunk(conn, "ch_eph_2", graph_id="gr_window", at=_T0 + timedelta(seconds=5))
        seed_chunk(conn, "ch_eph_3", graph_id="gr_window", at=_T0 + timedelta(seconds=4))
        seed_chunk(conn, "ch_live_a", graph_id="gr_window", at=_T0 + timedelta(seconds=3))
        seed_chunk(conn, "ch_live_b", graph_id="gr_window", at=_T0 + timedelta(seconds=2))
        seed_chunk(conn, "ch_live_c", graph_id="gr_window", at=_T0 + timedelta(seconds=1))

    _delete_chunk(hub, "ch_eph_1")
    _delete_chunk(hub, "ch_eph_2")
    _delete_chunk(hub, "ch_eph_3")

    full = hub.client.get("/api/chunks", params={"limit": MAX_LIMIT})
    assert full.status_code == 200, full.text
    full_ids = [c["chunk_id"] for c in full.json()["chunks"]]
    assert full_ids == ["ch_live_a", "ch_live_b", "ch_live_c"]

    paged_ids = [c["chunk_id"] for c in _all_pages(hub, limit=2)]

    assert paged_ids == full_ids
    assert len(set(paged_ids)) == len(paged_ids)


def test_live_holder_and_blocked_markings_survive_a_page_boundary(tmp_path: Path) -> None:
    """Live-holder/blocked derivation resolves a cause held off the page, so a marking must
    be identical whether its cause shares the page or not — forced here by putting every chunk on its own page (`limit=1`)."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": BUILD_REVIEW_DELIVER_YAML}).status_code == 201

    # (a) old + fresh holders of one pointer, over the `hub` source since its `web_url`
    # varies with the live holder — `default`'s ignores `live_holder` entirely.
    old_holder_id = hub.client.post("/api/chunks", json={"tokens": ["hub:1"]}).json()["chunk_id"]
    make_ready(hub, old_holder_id)
    build_id = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": old_holder_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    commit = [
        {
            "name": "w",
            "kind": "git_commit",
            "repo": "acme/widget",
            "branch_name": "b",
            "commit_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        }
    ]
    to_review = pass_node(hub, old_holder_id, build_id, 1, artifacts=commit)
    review_id = to_review["next_envelope"]["node"]["node_id"]
    assert report_lease(hub, old_holder_id, epoch=2, seq=1)["applied"] == [1]
    pass_node(hub, old_holder_id, review_id, 2, artifacts=[])
    hub.settle_turns()
    assert hub.client.get(f"/api/chunks/{old_holder_id}").json()["status"] == "done"

    hub.clock.advance(timedelta(seconds=1))
    live_holder_id = hub.client.post("/api/chunks", json={"tokens": ["hub:1"]}).json()["chunk_id"]
    make_ready(hub, live_holder_id)

    # (b) a separate dependent/prerequisite pair (the `tests/test_blocked_marking_api.py` shape).
    hub.clock.advance(timedelta(seconds=1))
    prerequisite_id = ingest(hub, [{"source": "default", "ref": "prereq"}])
    hub.clock.advance(timedelta(seconds=1))
    dependent_id = ingest(hub, [{"source": "default", "ref": "dependent"}])
    declare = hub.client.post(
        f"/api/chunks/{dependent_id}/dependencies", json={"prerequisite_chunk_id": prerequisite_id}
    )
    assert declare.status_code == 202, declare.text

    full = hub.client.get("/api/chunks", params={"limit": MAX_LIMIT})
    assert full.status_code == 200, full.text
    full_by_id = {c["chunk_id"]: c for c in full.json()["chunks"]}

    def hub_pointer_url(entry: dict[str, Any]) -> str | None:
        (ref,) = [w for w in entry["work_refs"] if w["source"] == "hub"]
        return ref["web_url"]

    # Both holders render the *same* live-holder link (the live one's own id) — proving
    # the terminal row still saw the live chunk that is what makes it non-live at all.
    assert hub_pointer_url(full_by_id[old_holder_id]) == f"/board/chunk/{live_holder_id}"
    assert hub_pointer_url(full_by_id[live_holder_id]) == f"/board/chunk/{live_holder_id}"
    assert full_by_id[dependent_id]["blocked"] == {"prerequisite_chunk_id": prerequisite_id, "unmet_count": 1}
    assert full_by_id[prerequisite_id]["blocked"] is None

    paged_by_id = {c["chunk_id"]: c for c in _all_pages(hub, limit=1)}

    for chunk_id in (old_holder_id, live_holder_id, dependent_id, prerequisite_id):
        assert paged_by_id[chunk_id]["work_refs"] == full_by_id[chunk_id]["work_refs"], chunk_id
        assert paged_by_id[chunk_id]["blocked"] == full_by_id[chunk_id]["blocked"], chunk_id


def test_limit_over_the_ceiling_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.get("/api/chunks", params={"limit": MAX_LIMIT + 1})
    assert resp.status_code == 422, resp.text


def test_limit_below_one_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.get("/api/chunks", params={"limit": 0})
    assert resp.status_code == 422, resp.text


def test_malformed_cursor_is_422_naming_the_cursor(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.get("/api/chunks", params={"cursor": "not-valid-base64!!!"})
    assert resp.status_code == 422, resp.text
    assert "malformed cursor" in resp.json()["detail"]


def test_next_cursor_is_non_null_until_the_final_page(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed(hub, 5)

    first = hub.client.get("/api/chunks", params={"limit": 3})
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert len(first_body["chunks"]) == 3
    assert first_body["next_cursor"] is not None

    second = hub.client.get("/api/chunks", params={"limit": 3, "cursor": first_body["next_cursor"]})
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert len(second_body["chunks"]) == 2
    assert second_body["next_cursor"] is None


def test_list_chunks_never_reads_standing_edges_or_records_tenant_wide(tmp_path: Path) -> None:
    from blizzard.hub.store.internal.chunk_dependencies_store import ChunkDependenciesStore
    from blizzard.hub.store.internal.chunk_record_store import ChunkRecordStore

    hub = build_hub(tmp_path)
    prerequisite_id = ingest(hub, [{"source": "default", "ref": "prereq"}])
    hub.clock.advance(timedelta(seconds=1))
    dependent_id = ingest(hub, [{"source": "default", "ref": "dependent"}])
    assert (
        hub.client.post(
            f"/api/chunks/{dependent_id}/dependencies", json={"prerequisite_chunk_id": prerequisite_id}
        ).status_code
        == 202
    )

    class _NoEdges(ChunkDependenciesStore):
        def list_standing_edges(self):
            raise AssertionError("list_standing_edges")

    class _NoList(ChunkRecordStore):
        def list_all(self):
            raise AssertionError("list_all")

    edges = _NoEdges.__new__(_NoEdges)
    edges.__dict__.update(hub.services.chunks.dependencies.__dict__)
    record = _NoList.__new__(_NoList)
    record.__dict__.update(hub.services.chunks.record.__dict__)
    serve_default_tenant(
        hub, replace(hub.services, chunks=replace(hub.services.chunks, dependencies=edges, record=record))
    )

    # Newest first: the dependent is alone on page one; its prerequisite is off the page.
    resp = hub.client.get("/api/chunks", params={"limit": 1})

    assert resp.status_code == 200, resp.text
    (row,) = resp.json()["chunks"]
    assert row["chunk_id"] == dependent_id
    assert row["blocked"] == {"prerequisite_chunk_id": prerequisite_id, "unmet_count": 1}


def test_list_page_reads_are_flat_as_deleted_chunks_pile_ahead_of_the_page(tmp_path: Path) -> None:
    measured = []
    for n in (2, 12):
        (tmp_path / str(n)).mkdir()
        hub = build_hub(tmp_path / str(n))
        with hub.engine.begin() as conn:
            seed_graph(conn, "gr_page", at=_T0)
            for i in range(n):
                seed_chunk(conn, f"ch_gone_{i}", graph_id="gr_page", at=_T0 + timedelta(seconds=100 + i))
            for i in range(3):
                seed_chunk(conn, f"ch_live_{i}", graph_id="gr_page", at=_T0 + timedelta(seconds=i))
        for i in range(n):
            _delete_chunk(hub, f"ch_gone_{i}")
        record = hub.services.chunks.record

        def page(record: Any = record) -> Any:
            return record.list_page(limit=2)

        assert [c.chunk_id for c in page().chunks] == ["ch_live_2", "ch_live_1"]
        assert page().next_cursor is not None
        measured.append((count_queries(hub.engine, page), count_rows_read(hub.engine, page)))
    assert measured[0] == measured[1]
