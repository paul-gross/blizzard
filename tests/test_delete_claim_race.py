"""The delete/claim race is atomic (issue #364, component tier).

Mirrors ``tests/test_edit_claim_race.py``'s own interleaving pattern: ``DeleteService``
and ``ClaimService`` share one claim lock, so a claim landing on a chunk mid-delete
can't interleave with the delete's own guard-check-then-write."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from blizzard.hub.store.internal import work_item_store as work_item_store_module
from tests.support import build_hub, ingest

pytestmark = pytest.mark.component


def _claim_body(chunk_id: str, runner: str = "r1") -> dict:
    return {"chunk_id": chunk_id, "runner_id": runner, "workspace_id": "w1", "environment_ids": ["env-a"]}


def test_a_claim_blocks_while_a_delete_holds_the_shared_lock_mid_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pause the delete mid-write and prove a concurrent claim blocks on the same lock;
    once released, the claim's own fresh re-read (issue #120) finds the chunk gone and
    refuses (409), never a route recorded against an ephemeral chunk."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])  # promote=True by default -> ready

    entered_write = threading.Event()
    release_write = threading.Event()
    real_record_deleted_row = work_item_store_module.record_deleted_row

    def _blocking_record_deleted_row(conn, chunk_id, *, by, at):  # type: ignore[no-untyped-def]
        entered_write.set()
        assert release_write.wait(timeout=5), "test never released the delete's write"
        return real_record_deleted_row(conn, chunk_id, by=by, at=at)

    monkeypatch.setattr(work_item_store_module, "record_deleted_row", _blocking_record_deleted_row)

    delete_result: dict[str, int] = {}

    def _delete() -> None:
        resp = hub.client.request("DELETE", f"/api/chunks/{chunk_id}", json={})
        delete_result["status"] = resp.status_code

    delete_thread = threading.Thread(target=_delete)
    delete_thread.start()
    assert entered_write.wait(timeout=5), "delete never reached its (patched) write"

    claim_response: dict[str, object] = {}

    def _claim() -> None:
        resp = hub.client.post("/api/fleet/routes", json=_claim_body(chunk_id))
        claim_response["status"] = resp.status_code
        claim_response["body"] = resp.json()

    claim_thread = threading.Thread(target=_claim)
    claim_thread.start()
    claim_thread.join(timeout=0.3)
    assert claim_thread.is_alive(), "the claim completed while the delete still held the shared lock — not atomic"
    assert hub.client.get(f"/api/chunks/{chunk_id}").status_code == 200, (
        "the chunk must not already read as gone while the delete still holds the lock"
    )

    release_write.set()
    delete_thread.join(timeout=5)
    claim_thread.join(timeout=5)

    assert delete_result["status"] == 202, delete_result
    # The delete's write landed first, under the lock — the claim's own fresh re-read
    # (`ClaimService._claim_locked`, issue #120) then finds the chunk gone and refuses.
    assert claim_response["status"] == 409, claim_response
    assert hub.client.get(f"/api/chunks/{chunk_id}").status_code == 404
