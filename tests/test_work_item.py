"""work-item pass-through read — body + comments per pointer, never stored (component tier)."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.domain.work import WorkItemAuthor, WorkRef
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore
from blizzard.hub.store.internal.chunk_work_refs_store import ChunkWorkRefsStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.work_sources.source import WorkItem
from tests.support import FakeWorkSource, build_hub, hub_store_connections, pointer_token, seed_work_item

pytestmark = pytest.mark.component

_POINTER = {"source": "widget", "ref": "42"}
_POINTER_2 = {"source": "widget", "ref": "43"}


def test_work_items_reads_body_and_comments_from_the_forge(tmp_path: Path) -> None:
    source = FakeWorkSource(
        name="widget", title="flaky test", body="please fix the flake", comments=["seen it too", "repro attached"]
    )
    hub = build_hub(tmp_path, work_sources={"widget": source})
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]

    resp = hub.client.get(f"/api/chunks/{chunk_id}/work-items")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["source"] == "widget"
    assert item["ref"] == "42"
    assert item["label"] == "widget#42"
    assert item["web_url"]
    assert item["title"] == "flaky test"
    assert item["body"] == "please fix the flake"
    assert item["comments"] == ["seen it too", "repro attached"]
    assert item["error"] is None
    assert item["fetched_at"]
    # The read went to the forge for this pointer — contents are fetched, not stored.
    assert source.fetched == ["42"]


def test_work_items_returns_one_entry_per_pointer(tmp_path: Path) -> None:
    """A grouped chunk carrying many pointers yields one entry per pointer, order preserved."""
    source = FakeWorkSource(
        name="widget",
        by_ref={
            "42": WorkItem(body="first issue", comments=["a"]),
            "43": WorkItem(body="second issue", comments=[]),
        },
    )
    hub = build_hub(tmp_path, work_sources={"widget": source})
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token(_POINTER), pointer_token(_POINTER_2)]}
    ).json()["chunk_id"]

    items = hub.client.get(f"/api/chunks/{chunk_id}/work-items").json()["items"]
    assert [i["ref"] for i in items] == ["42", "43"]
    assert [i["body"] for i in items] == ["first issue", "second issue"]


def test_work_items_degrades_per_pointer_when_the_forge_is_unreachable(tmp_path: Path) -> None:
    """One unreachable pointer surfaces as an ``error`` entry; the reachable one still reads."""
    source = FakeWorkSource(
        name="widget",
        by_ref={"42": WorkItem(title="reachable issue", body="reachable", comments=[])},
        fail_refs={"43"},
    )
    hub = build_hub(tmp_path, work_sources={"widget": source})
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token(_POINTER), pointer_token(_POINTER_2)]}
    ).json()["chunk_id"]

    resp = hub.client.get(f"/api/chunks/{chunk_id}/work-items")
    assert resp.status_code == 200
    ok, failed = resp.json()["items"]
    assert ok["title"] == "reachable issue" and ok["body"] == "reachable" and ok["error"] is None
    # A per-pointer forge failure nulls title alongside body — never a partial item.
    assert failed["title"] is None
    assert failed["body"] is None and failed["error"] and "43" in failed["error"]


def test_work_items_with_no_pointers_is_an_empty_list(tmp_path: Path) -> None:
    """A chunk with no pointers is the board's empty state — an empty list, 200, not a 404."""
    # Ingest guards against empty pointers at the front door (422), so mint the degenerate
    # empty-pointer chunk through the ingest service directly to prove the route still answers.
    hub = build_hub(tmp_path)
    graph = hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )
    chunk_id = hub.services.ingest.ingest([], graph=graph)

    resp = hub.client.get(f"/api/chunks/{chunk_id}/work-items")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_work_items_on_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.get("/api/chunks/ch_missing/work-items").status_code == 404


def test_work_items_carries_a_hub_pointer_s_author_and_priority_beside_a_forge_pointer(tmp_path: Path) -> None:
    """A mixed chunk: the hub pointer's entry carries author + stated priority; the forge
    pointer's entry carries neither, and its existing fields are unchanged (blizzard#362)."""
    forge = FakeWorkSource(name="widget", title="flaky test", body="please fix the flake")
    hub = build_hub(tmp_path, work_sources={"widget": forge})
    graph = hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )
    hub_store = hub_store_connections(hub.engine)
    items = WorkItemStore(hub_store)
    work_refs = ChunkWorkRefsStore(hub_store, hub.clock, facts=ChunkFactsStore(hub_store, hub.clock))
    author = WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_proposer", node_name="triage")
    hub_item = seed_work_item(
        items,
        graph_id=graph.graph_id,
        title="proposed by the fleet",
        body="please add a widget",
        author=author,
        stated_priority="high",
        at=hub.clock.now(),
    )
    # `seed_work_item` mints its own resting chunk holding the hub pointer alone
    # (blizzard#359) — grow *that* chunk with the forge pointer to avoid re-holding it.
    chunk_id = work_refs.find_live_holder(WorkRef(source="hub", ref=hub_item.ref))
    assert chunk_id is not None
    work_refs.add_work_refs(chunk_id, [WorkRef(source="widget", ref="42")], at=hub.clock.now())

    entries = {e["source"]: e for e in hub.client.get(f"/api/chunks/{chunk_id}/work-items").json()["items"]}

    hub_entry = entries["hub"]
    assert hub_entry["title"] == "proposed by the fleet"
    assert hub_entry["author"] == {
        "kind": "fleet",
        "user_id": None,
        "login": None,
        "runner_id": "runner-local",
        "chunk_id": "ch_proposer",
        "node_name": "triage",
    }
    assert hub_entry["stated_priority"] == "high"

    forge_entry = entries["widget"]
    assert forge_entry["title"] == "flaky test"
    assert forge_entry["author"] is None
    assert forge_entry["stated_priority"] is None


def test_work_items_isolates_an_unrecognized_stated_priority_to_that_entry(tmp_path: Path) -> None:
    """A source answering a ``stated_priority`` outside ``WorkItemPriority`` degrades that
    one entry to an ``error`` (blizzard#362) rather than 500ing the whole read — a non-hub
    ``IWorkSource`` is free to return any seam-legal string, valid or not."""
    source = FakeWorkSource(
        name="widget",
        by_ref={
            "42": WorkItem(title="reachable issue", body="reachable", comments=[]),
            "43": WorkItem(title="bad priority", body="b", comments=[], stated_priority="urgent"),
        },
    )
    hub = build_hub(tmp_path, work_sources={"widget": source})
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token(_POINTER), pointer_token(_POINTER_2)]}
    ).json()["chunk_id"]

    resp = hub.client.get(f"/api/chunks/{chunk_id}/work-items")
    assert resp.status_code == 200
    ok, bad = resp.json()["items"]
    assert ok["title"] == "reachable issue" and ok["error"] is None
    assert bad["title"] is None and bad["body"] is None
    assert bad["error"] and "urgent" in bad["error"]


def test_work_items_an_unresolvable_hub_pointer_still_carries_an_in_app_web_url(tmp_path: Path) -> None:
    """A hub pointer with no matching item row still resolves ``web_url`` — computed
    before ``fetch`` is attempted — while degrading to an ``error`` entry, same as any
    other pointer (blizzard#362)."""
    hub = build_hub(tmp_path, work_sources={"widget": FakeWorkSource(name="widget")})
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    hub_store = hub_store_connections(hub.engine)
    work_refs = ChunkWorkRefsStore(hub_store, hub.clock, facts=ChunkFactsStore(hub_store, hub.clock))
    work_refs.add_work_refs(chunk_id, [WorkRef(source="hub", ref="999")], at=hub.clock.now())

    entries = {e["source"]: e for e in hub.client.get(f"/api/chunks/{chunk_id}/work-items").json()["items"]}

    hub_entry = entries["hub"]
    assert hub_entry["error"] and "999" in hub_entry["error"]
    assert hub_entry["author"] is None
    assert hub_entry["web_url"] == f"/board/chunk/{chunk_id}"
