"""``GET /api/chunks`` — the bulk-read list path (component tier, blizzard#421).

Proves the route reads the fleet's facts and routes with one bulk query each, so the query
count is unchanged as fleet size grows and never reaches `load_facts`/`route_of` at all."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.pagination import MAX_LIMIT
from blizzard.hub.domain.work import ChunkFacts, WorkRef
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore
from blizzard.hub.store.internal.chunk_route_store import ChunkRouteStore
from blizzard.hub.store.internal.chunk_work_refs_store import ChunkWorkRefsStore
from blizzard.hub.store.internal.graph_store import GraphStore
from tests.support import build_hub, count_queries, hub_store_connections, ingest, seed_chunk, seed_graph
from tests.test_ingest_and_queue import _BUILD_REVIEW_DELIVER_YAML, _pass

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
        results[key] = len(resp.json()["chunks"])

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
    assert len(resp.json()["chunks"]) == 2
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
    by_source_ref = {(row["work_refs"][0]["source"], row["work_refs"][0]["ref"]): row for row in resp.json()["chunks"]}
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
    assert len(resp.json()["chunks"]) == 2
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
        return len(resp.json()["chunks"])

    few_count = count_queries(few.engine, lambda: call(few))
    many_count = count_queries(many.engine, lambda: call(many))

    assert few_count == many_count


# Keyset pagination (blizzard#526 D3/D4/D6) — sort-key ties, ephemeral windows, the
# whole-fleet-derived live-holder/blocked markings surviving a page boundary, and the
# route's own limit/cursor validation.


def _delete_chunk(hub, chunk_id: str) -> None:  # type: ignore[no-untyped-def]
    """``DELETE /api/chunks/{id}`` — ``httpx``'s own ``delete()`` refuses a ``json``
    keyword, so this goes through ``request`` instead (mirrors
    ``tests/test_chunk_delete_route.py``'s ``_delete_chunk``)."""
    resp = hub.client.request("DELETE", f"/api/chunks/{chunk_id}", json={})
    assert resp.status_code == 202, resp.text


def _all_pages(hub, *, limit: int) -> list[dict]:  # type: ignore[no-untyped-def, type-arg]
    """Walks ``GET /api/chunks`` at a fixed ``limit``, following ``next_cursor`` until
    it comes back null, and concatenates every page's own chunks in order."""
    chunks: list[dict] = []  # type: ignore[type-arg]
    cursor: str | None = None
    while True:
        params: dict[str, object] = {"limit": limit}
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
    """`chunk_id desc` is what makes the sort a total order when `minted_at` ties
    (blizzard#526 D4) — paging at `limit=1` through a fleet carrying two same-instant
    ties must reproduce a single large-limit read's order exactly, no dupes, no gaps."""
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
    """`list_page`'s SQL window filters ephemeral chunks out in Python after the read, so
    a window landing entirely on grouped-away/deleted rows must retry with a doubled
    window rather than short-paging (blizzard#526 D6): three deleted chunks sit at the
    very top of the order, ahead of the three live ones a `limit=2` page must still see."""
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
    """D6: live-holder and blocked-prerequisite derivation read the whole fleet even
    though only the page's own chunks render — so a chunk's marking must be identical
    whether the chunk that causes it (a competing pointer-holder, a prerequisite) shares
    its page or not. Forces every chunk onto its own page (`limit=1`) and compares
    against a single large-limit read."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _BUILD_REVIEW_DELIVER_YAML}).status_code == 201

    # (a) one pointer, two holders: an old, terminal chunk and a freshly re-ingested live
    # one — the exact `test_terminal_pointer_reingest_mints_a_fresh_chunk` shape, over the
    # `hub` work source so the rendered `web_url` actually varies with the live holder
    # (`HubWorkSource.web_url` is `None`-vs-link on it; the fake `default` source ignores
    # `live_holder` entirely).
    old_holder_id = hub.client.post("/api/chunks", json={"tokens": ["hub:1"]}).json()["chunk_id"]
    build_id = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": old_holder_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    commit = [{"name": "w", "kind": "git_commit", "repo": "acme/widget", "branch_name": "b", "commit_hash": "c"}]
    to_review = _pass(hub, old_holder_id, build_id, 1, artifacts=commit)
    review_id = to_review["next_envelope"]["node"]["node_id"]
    assert (
        hub.client.post(f"/api/fleet/chunks/{old_holder_id}/leases", json={"epoch": 2, "runner_id": "r1"}).status_code
        == 202
    )
    _pass(hub, old_holder_id, review_id, 2, artifacts=[])
    assert hub.client.get(f"/api/chunks/{old_holder_id}").json()["status"] == "done"

    hub.clock.advance(timedelta(seconds=1))
    live_holder_id = hub.client.post("/api/chunks", json={"tokens": ["hub:1"]}).json()["chunk_id"]

    # (b) a separate dependent/prerequisite pair (the `test_blocked_marking_api.py` shape).
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

    def hub_pointer_url(entry: dict) -> str | None:  # type: ignore[type-arg]
        (ref,) = [w for w in entry["work_refs"] if w["source"] == "hub"]
        return ref["web_url"]

    # Both the old, terminal holder and the fresh, live one render the *same* live-holder
    # link for the pointer they share — the live one's own id — proving the terminal
    # chunk's row saw the live chunk even though minting it is what makes the terminal
    # chunk's holding non-live in the first place.
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
