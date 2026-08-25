"""Chunk delete (component tier, blizzard#364) — ``DeleteService`` and the composite
``WorkItemStore.delete_chunk_and_withdraw_hub_items`` write, driven against a real,
migrated store. Every case here reaches the domain service and the stores directly, the
way ``tests/test_hub_work_source.py`` drives ``WorkItemEditService`` directly — the
``DELETE /chunks/{id}`` route itself (Phase 2) is covered over HTTP by
``tests/test_chunk_delete_route.py``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.delete import ChunkNotDeletable, DeleteService
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.queue import ChunkNotFound
from blizzard.hub.domain.work import (
    Chunk,
    ClosableWorkRef,
    WorkItemAuthor,
    WorkItemClosure,
    WorkRef,
)
from blizzard.hub.store.internal.chunk_store import ChunkStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support import build_hub, migrate_to, pointer_token, seed_graph, seed_work_item

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _stores(tmp_path: Path) -> tuple[ChunkStore, WorkItemStore, DeleteService, Engine]:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
    clock = FixedClock(_T0)
    chunks = ChunkStore(engine, clock)
    items = WorkItemStore(engine)
    delete = DeleteService(chunks=chunks, items=items, clock=clock)
    return chunks, items, delete, engine


def _mint(chunks: ChunkStore, chunk_id: str, *, work_refs: list[WorkRef] | None = None) -> Chunk:
    chunk = Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=work_refs or [], minted_at=_T0)
    chunks.mint(chunk)
    return chunk


# --- a deleted chunk is gone from every read (D6's widened ephemeral-id set) -------


def test_delete_removes_the_chunk_from_every_read(tmp_path: Path) -> None:
    chunks, _, delete, _ = _stores(tmp_path)
    pointer = WorkRef(source="default", ref="1")
    chunk = _mint(chunks, "ch_1", work_refs=[pointer])
    chunks.record_hub_artifact(
        "ch_1", node_id="nd_deliver", node_name="deliver", epoch=1, name="merged/widget", content="sha", at=_T0
    )
    # Sanity: landed and closable *before* the delete — proves the post-delete emptiness
    # below is the ephemeral exclusion at work, not a vacuously-empty read.
    assert chunks.closable_work_refs() == [ClosableWorkRef(chunk_id="ch_1", ref=pointer)]

    delete.delete(chunk, by="operator")

    assert chunks.get("ch_1") is None
    assert chunks.load_facts("ch_1") is None
    assert chunks.list_all() == []
    assert chunks.find_live_holder(pointer) is None
    assert chunks.live_work_refs() == {}
    assert chunks.closable_work_refs() == []


# --- refusal at every status outside GROUPABLE_STATUSES, success at both members ---


def _make_running(chunks: ChunkStore, chunk_id: str) -> None:
    chunks.record_route(
        Route(chunk_id=chunk_id, runner_id="r1", workspace_id="w1", environment_ids=[], created_at=_T0),
        token_hash="deadbeef",
        at=_T0,
    )


def _make_paused(chunks: ChunkStore, chunk_id: str) -> None:
    chunks.record_pause(chunk_id, paused=True, by="alice", at=_T0)


def _make_needs_human(chunks: ChunkStore, chunk_id: str) -> None:
    chunks.record_escalation(chunk_id, epoch=1, takeover_command="cd x && resume", at=_T0)


def _make_waiting_on_human(chunks: ChunkStore, chunk_id: str) -> None:
    chunks.record_question(
        question_id="qn_1",
        chunk_id=chunk_id,
        node_id="nd_a",
        session_id=None,
        runner_id="r1",
        epoch=1,
        question="continue?",
        options=[],
        asked_at=_T0,
    )


def _make_stopped(chunks: ChunkStore, chunk_id: str) -> None:
    chunks.record_stop(chunk_id, by="alice", at=_T0)


def _make_done(chunks: ChunkStore, chunk_id: str) -> None:
    chunks.record_completion(chunk_id, by="alice", at=_T0)


@pytest.mark.parametrize(
    "driver,expected_status",
    [
        (_make_running, "running"),
        (_make_paused, "paused"),
        (_make_needs_human, "needs_human"),
        (_make_waiting_on_human, "waiting_on_human"),
        (_make_stopped, "stopped"),
        (_make_done, "done"),
    ],
    ids=["running", "paused", "needs_human", "waiting_on_human", "stopped", "done"],
)
def test_delete_refuses_every_status_outside_groupable(tmp_path: Path, driver, expected_status: str) -> None:
    chunks, _, delete, _ = _stores(tmp_path)
    chunk = _mint(chunks, "ch_1")
    driver(chunks, "ch_1")

    with pytest.raises(ChunkNotDeletable) as excinfo:
        delete.delete(chunk, by="operator")

    assert excinfo.value.chunk_id == "ch_1"
    detail = str(excinfo.value)
    assert "ch_1" in detail
    assert expected_status in detail
    assert chunks.get("ch_1") is not None  # refused — nothing written, the chunk stands


def test_delete_succeeds_at_not_ready_and_ready(tmp_path: Path) -> None:
    chunks, _, delete, _ = _stores(tmp_path)
    not_ready = _mint(chunks, "ch_1")
    ready = _mint(chunks, "ch_2")
    chunks.record_promote("ch_2", at=_T0)

    delete.delete(not_ready, by="operator")
    delete.delete(ready, by="operator")

    assert chunks.get("ch_1") is None
    assert chunks.get("ch_2") is None


# --- the activity feed: the deletion row, its actor, and nothing else for that chunk


def test_activity_feed_shows_the_deletion_with_its_actor_and_no_other_row(tmp_path: Path) -> None:
    chunks, _, delete, _ = _stores(tmp_path)
    chunk = _mint(chunks, "ch_1")
    chunks.record_promote("ch_1", at=_at(1))  # a row that would otherwise show for ch_1

    delete.delete(chunk, by="paul")

    rows = [r for r in chunks.activity_facts_since(_T0, limit=50) if r.chunk_id == "ch_1"]
    assert len(rows) == 1
    row = rows[0]
    assert row.type == "chunk-changed"
    assert row.cause == "deleted"
    assert row.by == "paul"
    assert row.key.startswith("chunk_deleted:")


def test_activity_feed_leaves_a_grouped_chunks_history_showing(tmp_path: Path) -> None:
    """D6: only ``deleted`` chunk ids are excluded from the feed's other blocks — a
    grouped chunk's history is existing, unchanged behavior."""
    chunks, _, _, _ = _stores(tmp_path)
    _mint(chunks, "ch_1")
    _mint(chunks, "ch_2")
    chunks.record_grouped("ch_2", grouped_into="ch_1", at=_at(1))

    rows = [r for r in chunks.activity_facts_since(_T0, limit=50) if r.chunk_id == "ch_2"]
    causes = {r.cause for r in rows}
    assert "minted" in causes
    assert "grouped" in causes


# --- D4: only the chunk's open hub: pointer(s) are withdrawn; a forge: pointer stands


def test_delete_withdraws_only_open_hub_pointers_leaving_a_forge_pointer_untouched(tmp_path: Path) -> None:
    chunks, items, delete, _ = _stores(tmp_path)
    item = seed_work_item(items, graph_id="gr_1", author=WorkItemAuthor.user("u_1"), at=_T0)
    chunk_id = f"ch_{item.ref}"
    forge_pointer = WorkRef(source="forge", ref="99")
    chunks.add_work_refs(chunk_id, [forge_pointer], at=_T0)
    mixed = chunks.get(chunk_id)
    assert mixed is not None
    assert {r.source for r in mixed.work_refs} == {"hub", "forge"}

    delete.delete(mixed, by="operator")

    closed = items.get("hub", item.ref)
    assert closed is not None and closed.closure == WorkItemClosure.WITHDRAWN
    assert items.get("forge", "99") is None  # never a work_items row — untouched, not erroneously closed
    assert chunks.get(chunk_id) is None


# --- idempotent-by-guard: a repeated direct delete writes nothing a second time ----


def test_repeated_delete_writes_nothing_a_second_time(tmp_path: Path) -> None:
    chunks, _, delete, _ = _stores(tmp_path)
    chunk = _mint(chunks, "ch_1")

    first_id = delete.delete(chunk, by="operator")
    with pytest.raises(ChunkNotFound):
        delete.delete(chunk, by="operator")

    rows = [r for r in chunks.activity_facts_since(_T0, limit=50) if r.chunk_id == "ch_1" and r.cause == "deleted"]
    assert len(rows) == 1
    assert rows[0].key == f"chunk_deleted:{first_id}"


# --- assumption 13 (standing, unchanged) — re-ingest after delete -----------------


def test_reingest_after_delete_a_forge_pointer_mints_a_fresh_chunk_reading_normally(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    pointer = {"source": "default", "ref": "1"}
    first = hub.client.post("/api/chunks", json={"tokens": [pointer_token(pointer)]}).json()
    chunks = ChunkStore(hub.engine, hub.clock)
    items = WorkItemStore(hub.engine)
    delete = DeleteService(chunks=chunks, items=items, clock=hub.clock)
    chunk = chunks.get(first["chunk_id"])
    assert chunk is not None
    delete.delete(chunk, by="operator")

    second = hub.client.post("/api/chunks", json={"tokens": [pointer_token(pointer)]})
    assert second.status_code == 201, second.text
    new_chunk_id = second.json()["chunk_id"]
    assert new_chunk_id != first["chunk_id"]

    entries = hub.client.get(f"/api/chunks/{new_chunk_id}/work-items").json()["items"]
    assert len(entries) == 1
    assert entries[0]["error"] is None
    assert entries[0]["title"] == "issue title"


def test_reingest_after_delete_a_withdrawn_hub_ref_degrades_to_an_error_entry(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    created = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()
    ref, chunk_id = created["ref"], created["chunk_id"]
    assert hub.client.delete(f"/api/work-sources/hub/items/{ref}").status_code == 200  # withdraws + deletes
    assert hub.client.get(f"/api/chunks/{chunk_id}").status_code == 404

    reingested = hub.client.post("/api/chunks", json={"tokens": [f"hub:{ref}"]})
    assert reingested.status_code == 201, reingested.text
    new_chunk_id = reingested.json()["chunk_id"]

    entries = hub.client.get(f"/api/chunks/{new_chunk_id}/work-items").json()["items"]
    assert len(entries) == 1
    assert entries[0]["error"] is not None
    assert ref in entries[0]["error"]
