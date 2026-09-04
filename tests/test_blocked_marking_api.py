"""The blocked marking on the four read surfaces (issue #457) — the derivation of
``derive_blocked_markings`` proven end to end over HTTP.

A marked chunk's ``status``, rank, and ranked list are untouched, and grouping, deletion,
and the pre-claim property edit still admit it — the marking changes nothing about what a
chunk may do, only what it says about why it cannot yet be claimed."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.work import ChunkFacts
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore
from tests.support import HubHarness, build_hub, count_queries, hub_store_connections, ingest

pytestmark = pytest.mark.component

_DEPENDENT = {"source": "default", "ref": "dependent"}
_PREREQUISITE = {"source": "default", "ref": "prereq"}


def _declare(hub: HubHarness, dependent_id: str, prerequisite_id: str):  # type: ignore[no-untyped-def]
    resp = hub.client.post(f"/api/chunks/{dependent_id}/dependencies", json={"prerequisite_chunk_id": prerequisite_id})
    assert resp.status_code == 202, resp.text
    return resp.json()


def _release(hub: HubHarness, dependent_id: str, prerequisite_id: str) -> None:
    resp = hub.client.post(
        f"/api/chunks/{dependent_id}/dependencies/release", json={"prerequisite_chunk_id": prerequisite_id}
    )
    assert resp.status_code == 202, resp.text


def _list_entry(hub: HubHarness, chunk_id: str) -> dict:  # type: ignore[type-arg]
    resp = hub.client.get("/api/chunks")
    assert resp.status_code == 200, resp.text
    (entry,) = [c for c in resp.json() if c["chunk_id"] == chunk_id]
    return entry


def _detail(hub: HubHarness, chunk_id: str) -> dict:  # type: ignore[type-arg]
    resp = hub.client.get(f"/api/chunks/{chunk_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _backlog_entry(hub: HubHarness, chunk_id: str) -> dict:  # type: ignore[type-arg]
    resp = hub.client.get("/api/backlog")
    assert resp.status_code == 200, resp.text
    (entry,) = [e for e in resp.json()["entries"] if e["chunk_id"] == chunk_id]
    return entry


def test_list_and_detail_carry_the_marking_beside_an_unchanged_status(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [_DEPENDENT])
    prerequisite_id = ingest(hub, [_PREREQUISITE])
    _declare(hub, dependent_id, prerequisite_id)

    summary = _list_entry(hub, dependent_id)
    detail = _detail(hub, dependent_id)

    assert summary["status"] == "ready"
    assert summary["blocked"] == {"prerequisite_chunk_id": prerequisite_id}
    assert detail["status"] == "ready"
    assert detail["blocked"] == {"prerequisite_chunk_id": prerequisite_id}


def test_unblocked_chunks_carry_no_marking(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_DEPENDENT])

    assert _list_entry(hub, chunk_id)["blocked"] is None
    assert _detail(hub, chunk_id)["blocked"] is None


def test_queue_and_backlog_entries_carry_the_marking_and_keep_their_rank(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first_id = ingest(hub, [{"source": "default", "ref": "first"}])
    dependent_id = ingest(hub, [_DEPENDENT])
    prerequisite_id = ingest(hub, [_PREREQUISITE])
    _declare(hub, dependent_id, prerequisite_id)

    resp = hub.client.get("/api/queue")
    assert resp.status_code == 200, resp.text
    entries = {e["chunk_id"]: e for e in resp.json()["entries"]}

    # Unchanged ranked order: the dependent still sits at the position promotion order
    # gave it, second of three, not moved for being blocked.
    assert entries[first_id]["position"] == 0
    assert entries[dependent_id]["position"] == 1
    assert entries[prerequisite_id]["position"] == 2
    assert entries[first_id]["blocked"] is None
    assert entries[dependent_id]["blocked"] == {"prerequisite_chunk_id": prerequisite_id}
    assert entries[prerequisite_id]["blocked"] is None


def test_backlog_entries_carry_the_marking(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [_DEPENDENT], promote=False)
    prerequisite_id = ingest(hub, [_PREREQUISITE], promote=False)
    _declare(hub, dependent_id, prerequisite_id)

    entry = _backlog_entry(hub, dependent_id)

    assert entry["blocked"] == {"prerequisite_chunk_id": prerequisite_id}


def test_marking_clears_once_the_prerequisite_completes(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [_DEPENDENT])
    prerequisite_id = ingest(hub, [_PREREQUISITE])
    _declare(hub, dependent_id, prerequisite_id)
    assert _list_entry(hub, dependent_id)["blocked"] is not None

    resp = hub.client.post(f"/api/chunks/{prerequisite_id}/complete", json={})
    assert resp.status_code == 202, resp.text

    assert _list_entry(hub, dependent_id)["blocked"] is None
    assert _detail(hub, dependent_id)["blocked"] is None


def test_marking_clears_once_the_edge_is_released(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [_DEPENDENT])
    prerequisite_id = ingest(hub, [_PREREQUISITE])
    _declare(hub, dependent_id, prerequisite_id)
    assert _list_entry(hub, dependent_id)["blocked"] is not None

    _release(hub, dependent_id, prerequisite_id)

    assert _list_entry(hub, dependent_id)["blocked"] is None
    assert _detail(hub, dependent_id)["blocked"] is None


def test_a_blocked_prerequisite_is_named_without_walking_its_own_chain(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_a = ingest(hub, [{"source": "default", "ref": "a"}])
    chunk_b = ingest(hub, [{"source": "default", "ref": "b"}])
    chunk_c = ingest(hub, [{"source": "default", "ref": "c"}])
    _declare(hub, chunk_a, chunk_b)
    _declare(hub, chunk_b, chunk_c)

    assert _list_entry(hub, chunk_a)["blocked"] == {"prerequisite_chunk_id": chunk_b}
    assert _list_entry(hub, chunk_b)["blocked"] == {"prerequisite_chunk_id": chunk_c}


def test_grouping_deletion_and_the_pre_claim_edit_still_admit_a_blocked_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [_DEPENDENT], promote=False)
    prerequisite_id = ingest(hub, [_PREREQUISITE], promote=False)
    _declare(hub, dependent_id, prerequisite_id)
    assert _list_entry(hub, dependent_id)["blocked"] is not None

    patch_resp = hub.client.patch(f"/api/chunks/{dependent_id}", json={"default_effort": "high"})
    assert patch_resp.status_code == 202, patch_resp.text
    assert patch_resp.json()["default_effort"] == "high"

    other_id = ingest(hub, [{"source": "default", "ref": "mergee"}], promote=False)
    group_resp = hub.client.post(f"/api/chunks/{dependent_id}/group", json={"merge_chunk_ids": [other_id]})
    assert group_resp.status_code == 200, group_resp.text

    delete_resp = hub.client.request("DELETE", f"/api/chunks/{dependent_id}", json={})
    assert delete_resp.status_code == 202, delete_resp.text
    assert hub.services.chunks.dependencies.standing_edge(dependent_id, prerequisite_id) is None


def test_a_completed_dependent_derives_no_marking_despite_its_standing_edge(tmp_path: Path) -> None:
    """Review round 1 F1: hand-completing the dependent while its prerequisite stays
    unmet must not leave a `done` chunk reporting itself `blocked` — that question no
    longer applies once the dependent is past the pre-claim window, even though the edge
    itself is still standing (never released)."""
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [_DEPENDENT])
    prerequisite_id = ingest(hub, [_PREREQUISITE])
    _declare(hub, dependent_id, prerequisite_id)

    resp = hub.client.post(f"/api/chunks/{dependent_id}/complete", json={})
    assert resp.status_code == 202, resp.text

    summary = _list_entry(hub, dependent_id)
    detail = _detail(hub, dependent_id)
    assert summary["status"] == "done"
    assert summary["blocked"] is None
    assert detail["status"] == "done"
    assert detail["blocked"] is None


class _CountingFactsStore(ChunkFactsStore):
    """Counts calls to the bulk and per-chunk facts reads, the ``test_list_chunks_bulk_reads.py``
    shape, so a test can pin which shape the detail route's blocked-marking derivation reaches."""

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


def test_detail_routes_blocked_derivation_never_reaches_load_all_facts(tmp_path: Path) -> None:
    """Review round 1 F5, round 2 F1: ``GET /api/chunks/{id}``'s blocked-marking and
    neighborhood (issue #462) share one standing-edges read and one resulting statuses map,
    so a prerequisite named by both costs one `load_facts` call, not two — only bounded
    per-chunk reads, never the bulk `load_all_facts` seam."""
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [_DEPENDENT], promote=False)
    prerequisite_id = ingest(hub, [_PREREQUISITE], promote=False)
    _declare(hub, dependent_id, prerequisite_id)

    counting = _CountingFactsStore(hub_store_connections(hub.engine), hub.clock)
    assert hub.app is not None
    hub.app.state.services = replace(hub.services, chunks=replace(hub.services.chunks, facts=counting))

    resp = hub.client.get(f"/api/chunks/{dependent_id}")

    assert resp.status_code == 200, resp.text
    assert resp.json()["blocked"] == {"prerequisite_chunk_id": prerequisite_id}
    assert counting.load_all_facts_calls == 0
    # the dependent's own facts, and the one shared read of its one prerequisite's facts.
    assert counting.load_facts_calls == 2


def test_detail_routes_facts_reads_are_bounded_by_its_own_edges_not_fleet_size(tmp_path: Path) -> None:
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small = build_hub(tmp_path / "small")
    dependent_id = ingest(small, [_DEPENDENT], promote=False)
    prerequisite_id = ingest(small, [_PREREQUISITE], promote=False)
    _declare(small, dependent_id, prerequisite_id)

    large = build_hub(tmp_path / "large")
    large_dependent_id = ingest(large, [_DEPENDENT], promote=False)
    large_prerequisite_id = ingest(large, [_PREREQUISITE], promote=False)
    _declare(large, large_dependent_id, large_prerequisite_id)
    for i in range(8):  # 9x the small fleet, none of it named by the dependent's own edges
        ingest(large, [{"source": "default", "ref": f"filler-{i}"}])

    def call(hub: HubHarness, chunk_id: str) -> None:
        resp = hub.client.get(f"/api/chunks/{chunk_id}")
        assert resp.status_code == 200, resp.text

    small_count = count_queries(small.engine, lambda: call(small, dependent_id))
    large_count = count_queries(large.engine, lambda: call(large, large_dependent_id))

    assert small_count == large_count
