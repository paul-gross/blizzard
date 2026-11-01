"""``POST /chunks/{id}/demote`` and the anchored ``POST /chunks/{id}/promote`` (component tier): a ready chunk
returns to the backlog where it was told to land and keeps everything it was minted with; a replay writes nothing;
a claimed or ended chunk is refused naming its status; a claim racing a demote resolves one way."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from blizzard.hub.store.internal import chunk_queue_store as chunk_queue_store_module
from tests.support import HubHarness, build_hub, ingest, report_lease

pytestmark = pytest.mark.component


def _chunk(hub: HubHarness, ref: str, *, promote: bool) -> str:
    return ingest(hub, [{"source": "default", "ref": ref}], promote=promote)


def _ids(hub: HubHarness, path: str) -> list[str]:
    resp = hub.client.get(path)
    assert resp.status_code == 200, resp.text
    return [e["chunk_id"] for e in resp.json()["entries"]]


def _status(hub: HubHarness, chunk_id: str) -> str:
    return hub.client.get(f"/api/chunks/{chunk_id}").json()["status"]


def _claim(hub: HubHarness, chunk_id: str, runner: str = "r1") -> Any:
    return hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": runner, "workspace_id": "w1", "environment_ids": ["env-a"]},
    )


def test_demote_returns_a_ready_chunk_to_the_backlog_tail(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resting = _chunk(hub, "1", promote=False)
    ready = _chunk(hub, "2", promote=True)

    resp = hub.client.post(f"/api/chunks/{ready}/demote")

    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "not_ready"
    assert _ids(hub, "/api/backlog") == [resting, ready]
    assert _ids(hub, "/api/queue") == []


def test_demote_after_an_anchor_lands_in_its_slot(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first, second = _chunk(hub, "1", promote=False), _chunk(hub, "2", promote=False)
    ready = _chunk(hub, "3", promote=True)
    # Mint instants tie on the test clock; fix the backlog order explicitly.
    assert hub.client.put("/api/backlog", json={"chunk_ids": [first, second]}).status_code == 200

    resp = hub.client.post(f"/api/chunks/{ready}/demote", json={"after_chunk_id": first})

    assert resp.status_code == 202, resp.text
    assert _ids(hub, "/api/backlog") == [first, ready, second]


def test_demote_to_top_lands_at_the_head(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resting = _chunk(hub, "1", promote=False)
    ready = _chunk(hub, "2", promote=True)

    assert hub.client.post(f"/api/chunks/{ready}/demote", json={"to_top": True}).status_code == 202
    assert _ids(hub, "/api/backlog") == [ready, resting]


def test_demote_keeps_the_chunks_identity(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ready = _chunk(hub, "1", promote=True)
    before = hub.client.get(f"/api/chunks/{ready}").json()

    assert hub.client.post(f"/api/chunks/{ready}/demote").status_code == 202

    after = hub.client.get(f"/api/chunks/{ready}").json()
    moved = {"status": None, "promotable": None}
    assert {**after, **moved} == {**before, **moved}
    assert before["status"] == "ready" and after["status"] == "not_ready"
    assert before["promotable"] is False and after["promotable"] is True


def test_demote_is_a_no_op_on_a_chunk_not_promoted(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resting = _chunk(hub, "1", promote=False)

    resp = hub.client.post(f"/api/chunks/{resting}/demote")

    assert resp.status_code == 202
    assert resp.json()["status"] == "not_ready"
    assert hub.services.chunks.queue.queue_positions([resting]) == {}


def test_demote_twice_is_a_replay(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ready = _chunk(hub, "1", promote=True)
    assert hub.client.post(f"/api/chunks/{ready}/demote").status_code == 202
    position = hub.services.chunks.queue.queue_positions([ready])

    assert hub.client.post(f"/api/chunks/{ready}/demote").status_code == 202
    assert hub.services.chunks.queue.queue_positions([ready]) == position


def test_demote_refuses_a_claimed_chunk_naming_its_status(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _chunk(hub, "1", promote=True)
    assert _claim(hub, chunk_id).status_code == 201
    report_lease(hub, chunk_id, epoch=1, seq=1)

    resp = hub.client.post(f"/api/chunks/{chunk_id}/demote")

    assert resp.status_code == 409
    assert resp.json()["detail"] == f"chunk {chunk_id} is running, not demotable"


def test_demote_of_an_unknown_chunk_is_404(tmp_path: Path) -> None:
    assert build_hub(tmp_path).client.post("/api/chunks/ch_missing/demote").status_code == 404


def test_demote_with_an_anchor_outside_the_backlog_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ready, other = _chunk(hub, "1", promote=True), _chunk(hub, "2", promote=True)

    resp = hub.client.post(f"/api/chunks/{ready}/demote", json={"after_chunk_id": other})

    assert resp.status_code == 409
    assert _status(hub, ready) == "ready"


@pytest.mark.parametrize("verb", ["demote", "promote"])
def test_a_body_naming_both_an_anchor_and_the_top_is_422(tmp_path: Path, verb: str) -> None:
    hub = build_hub(tmp_path)
    ready, other = _chunk(hub, "1", promote=True), _chunk(hub, "2", promote=True)

    resp = hub.client.post(f"/api/chunks/{ready}/{verb}", json={"after_chunk_id": other, "to_top": True})

    assert resp.status_code == 422


def test_demote_anchored_on_itself_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ready = _chunk(hub, "1", promote=True)

    assert hub.client.post(f"/api/chunks/{ready}/demote", json={"after_chunk_id": ready}).status_code == 422


def test_re_promote_after_a_demote_lands_at_the_queue_tail(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first, second = _chunk(hub, "1", promote=True), _chunk(hub, "2", promote=True)
    assert hub.client.post(f"/api/chunks/{first}/demote").status_code == 202

    assert hub.client.post(f"/api/chunks/{first}/promote").status_code == 202

    assert _ids(hub, "/api/queue") == [second, first]


def test_anchored_promote_lands_after_its_anchor(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first, second = _chunk(hub, "1", promote=True), _chunk(hub, "2", promote=True)
    resting = _chunk(hub, "3", promote=False)

    resp = hub.client.post(f"/api/chunks/{resting}/promote", json={"after_chunk_id": first})

    assert resp.status_code == 202, resp.text
    assert _ids(hub, "/api/queue") == [first, resting, second]


def test_promote_to_top_lands_at_the_head(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = _chunk(hub, "1", promote=True)
    resting = _chunk(hub, "2", promote=False)

    assert hub.client.post(f"/api/chunks/{resting}/promote", json={"to_top": True}).status_code == 202
    assert _ids(hub, "/api/queue") == [resting, first]


def test_anchored_promote_with_an_anchor_outside_the_queue_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resting, other = _chunk(hub, "1", promote=False), _chunk(hub, "2", promote=False)

    resp = hub.client.post(f"/api/chunks/{resting}/promote", json={"after_chunk_id": other})

    assert resp.status_code == 409
    assert _status(hub, resting) == "not_ready"


def test_a_demote_publishes_a_chunk_changed_activity_row(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ready = _chunk(hub, "1", promote=True)
    assert hub.client.post(f"/api/chunks/{ready}/demote").status_code == 202

    entries = hub.client.get("/api/activity").json()["activity"]
    demoted = [e for e in entries if e.get("cause") == "demoted" and e.get("chunk_id") == ready]
    assert len(demoted) == 1


def test_a_claim_blocks_while_a_demote_holds_the_row_lock_mid_write(tmp_path: Path) -> None:
    """Pause the demote mid-write (after its row lock, before its rows land) and prove a concurrent claim on the same
    chunk blocks; once released, the claim re-reads fresh and refuses against the now-demoted chunk."""
    hub = build_hub(tmp_path)
    chunk_id = _chunk(hub, "1", promote=True)

    entered_write = threading.Event()
    release_write = threading.Event()
    real_insert = chunk_queue_store_module.insert_demote_rows

    def _blocking_insert(conn, chunk_id, **kwargs):
        entered_write.set()
        assert release_write.wait(timeout=5), "test never released the demote's write"
        return real_insert(conn, chunk_id, **kwargs)

    chunk_queue_store_module.insert_demote_rows = _blocking_insert
    try:
        demote_result: dict[str, int] = {}

        def _demote() -> None:
            demote_result["status"] = hub.client.post(f"/api/chunks/{chunk_id}/demote").status_code

        demote_thread = threading.Thread(target=_demote)
        demote_thread.start()
        assert entered_write.wait(timeout=5), "demote never reached its (patched) write"

        claim: dict[str, int] = {}

        def _claim_it() -> None:
            claim["status"] = _claim(hub, chunk_id, runner="r2").status_code

        claim_thread = threading.Thread(target=_claim_it)
        claim_thread.start()
        claim_thread.join(timeout=0.3)
        assert claim_thread.is_alive(), "the claim completed while the demote still held the row lock — not atomic"

        release_write.set()
        demote_thread.join(timeout=5)
        claim_thread.join(timeout=5)
    finally:
        chunk_queue_store_module.insert_demote_rows = real_insert

    assert demote_result["status"] == 202
    assert claim["status"] == 409
    assert _status(hub, chunk_id) == "not_ready"
