"""``GET /api/work-items`` — the bulk pass-through read, one path with the per-chunk read (component tier)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from blizzard.hub.domain.chunk.model import WorkItemAuthor, WorkRef
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore
from blizzard.hub.store.internal.chunk_work_refs_store import ChunkWorkRefsStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.work_sources.source import WorkItem
from tests.support import (
    FakeBatchWorkSource,
    FakeWorkSource,
    build_hub,
    chunk_stores,
    count_queries,
    hub_store_connections,
    pointer_token,
    seed_runner,
    seed_work_item,
)

pytestmark = pytest.mark.component


def _chunk(hub: Any, source: str, ref: str) -> str:
    return str(
        hub.client.post("/api/chunks", json={"tokens": [pointer_token({"source": source, "ref": ref})]}).json()[
            "chunk_id"
        ]
    )


def _bulk(hub: Any, chunk_ids: list[str], **params: str) -> Any:
    return hub.client.get("/api/work-items", params=[("chunk_id", c) for c in chunk_ids] + list(params.items()))


def test_one_bulk_call_reads_each_source_once_and_matches_the_single_chunk_read(tmp_path: Path) -> None:
    source = FakeBatchWorkSource(
        name="widget",
        by_ref={"1": WorkItem(title="one", body="b1", comments=["c1"]), "2": WorkItem(title="two", body="b2")},
    )
    hub = build_hub(tmp_path, work_sources={"widget": source})
    a, b = _chunk(hub, "widget", "1"), _chunk(hub, "widget", "2")

    resp = _bulk(hub, [a, b])
    assert resp.status_code == 200, resp.text
    chunks = resp.json()["chunks"]
    assert [(sorted(refs), comments) for refs, comments in source.batches] == [(["1", "2"], False)]
    for chunk_id in (a, b):
        single = hub.client.get(f"/api/chunks/{chunk_id}/work-items").json()["items"]
        (bulk,) = chunks[chunk_id]
        (alone,) = single
        # Everything but the clock stamp and the comments, which the bulk read leaves empty unasked.
        assert {**bulk, "fetched_at": "", "comments": []} == {**alone, "fetched_at": "", "comments": []}
    assert chunks[a][0]["comments"] == []


def test_include_comments_asks_the_source_for_them(tmp_path: Path) -> None:
    source = FakeBatchWorkSource(name="widget", by_ref={"1": WorkItem(title="one", body="b", comments=["c1"])})
    hub = build_hub(tmp_path, work_sources={"widget": source})
    a = _chunk(hub, "widget", "1")
    resp = _bulk(hub, [a], include_comments="true")
    assert resp.json()["chunks"][a][0]["comments"] == ["c1"]
    assert source.batches == [(["1"], True)]


def test_more_than_a_hundred_chunk_ids_is_a_422_and_a_hundred_is_not(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ids = [f"ch_missing{i}" for i in range(101)]
    assert _bulk(hub, ids).status_code == 422
    assert _bulk(hub, ids[:100]).status_code == 200
    assert _bulk(hub, []).status_code == 422


def test_an_unknown_chunk_id_is_omitted(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"widget": FakeWorkSource(name="widget")})
    a = _chunk(hub, "widget", "1")
    resp = _bulk(hub, [a, "ch_nope"])
    assert resp.status_code == 200
    assert list(resp.json()["chunks"]) == [a]


def test_an_unconfigured_source_and_a_failing_source_each_error_only_their_own_refs(tmp_path: Path) -> None:
    failing = FakeBatchWorkSource(name="flaky", fail_refs={"9"})
    ok = FakeBatchWorkSource(name="widget", title="fine")
    hub = build_hub(tmp_path, work_sources={"widget": ok, "flaky": failing})
    good, bad, gone = _chunk(hub, "widget", "1"), _chunk(hub, "flaky", "9"), _chunk(hub, "widget", "2")
    # A pointer to a source nothing configures: seed it onto a chunk directly.
    hub_store = hub_store_connections(hub.engine)
    refs = ChunkWorkRefsStore(hub_store, hub.clock, facts=ChunkFactsStore(hub_store, hub.clock))
    with chunk_stores(hub.engine, hub.clock).exclusive.locked([gone]) as handle:
        refs.add_work_refs_locked(handle, gone, [WorkRef(source="retired", ref="5")], at=hub.clock.now())

    chunks = _bulk(hub, [good, bad, gone]).json()["chunks"]
    assert chunks[good][0]["title"] == "fine" and chunks[good][0]["error"] is None
    assert chunks[bad][0]["title"] is None and "9" in chunks[bad][0]["error"]
    by_source = {e["source"]: e for e in chunks[gone]}
    assert by_source["widget"]["title"] == "fine"
    assert "retired" in by_source["retired"]["error"]


def test_a_source_with_no_batch_reader_falls_back_to_a_fetch_per_ref(tmp_path: Path) -> None:
    source = FakeWorkSource(name="widget", title="plain")
    hub = build_hub(tmp_path, work_sources={"widget": source})
    a, b = _chunk(hub, "widget", "1"), _chunk(hub, "widget", "2")
    chunks = _bulk(hub, [a, b]).json()["chunks"]
    assert sorted(source.fetched) == ["1", "2"]
    assert chunks[a][0]["title"] == "plain" and chunks[b][0]["title"] == "plain"


def test_hub_authored_refs_cost_a_bounded_number_of_statements(tmp_path: Path) -> None:
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()

    def seeded(root: Path, count: int) -> tuple[Any, list[str]]:
        hub = build_hub(root)
        graph = hub.services.graph_mint.ensure_default(
            hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
        )
        store = hub_store_connections(hub.engine)
        items = WorkItemStore(store)
        refs = ChunkWorkRefsStore(store, hub.clock, facts=ChunkFactsStore(store, hub.clock))
        seed_runner(hub, "runner-x", name="r-x", register=False)
        ids: list[str] = []
        for i in range(count):
            item = seed_work_item(
                items,
                graph_id=graph.graph_id,
                title=f"t{i}",
                body="b",
                author=WorkItemAuthor.fleet(runner_id="runner-x", chunk_id="ch_p", node_name="triage"),
                at=hub.clock.now(),
            )
            holder = refs.find_live_holder(WorkRef(source="hub", ref=item.ref))
            assert holder is not None
            ids.append(holder)
        return hub, ids

    small, small_ids = seeded(tmp_path / "small", 2)
    large, large_ids = seeded(tmp_path / "large", 8)

    def call(hub: Any, ids: list[str]) -> None:
        resp = _bulk(hub, ids)
        assert resp.status_code == 200, resp.text
        assert all(entry[0]["author"]["runner_name"] == "r-x" for entry in resp.json()["chunks"].values())

    assert count_queries(small.engine, lambda: call(small, small_ids)) == count_queries(
        large.engine, lambda: call(large, large_ids)
    )
