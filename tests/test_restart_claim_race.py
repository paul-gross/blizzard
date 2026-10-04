"""The restart/claim race is atomic (component tier).

Mirrors ``tests/test_edit_claim_race.py``'s own interleaving pattern: ``RestartService``
and ``ClaimService`` now serialize through the same locked row transaction
(``bzh:store-exclusive-write``), not a shared in-process lock — a claim landing on a
chunk mid-restart can't interleave with the restart's own read-then-write."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import cast

import pytest

from blizzard.hub.store.internal.chunk_movement_store import ChunkMovementStore
from tests.support import build_hub, ingest

pytestmark = pytest.mark.component


def _claim_body(chunk_id: str, runner: str = "r1") -> dict:
    return {"chunk_id": chunk_id, "runner_id": runner, "workspace_id": "w1", "environment_ids": ["env-a"]}


def test_a_claim_blocks_while_a_restart_holds_the_row_lock_mid_write(tmp_path: Path) -> None:
    """Pause the restart mid-write and prove a concurrent claim on the same chunk blocks
    on the same row lock; once released, the claim lands against the now-restarted
    epoch, never a torn read of the two."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])

    entered_write = threading.Event()
    release_write = threading.Event()
    movement = cast(ChunkMovementStore, hub.services.chunks.movement)
    real_record_restart_conn = movement._record_restart_conn

    def _blocking_record_restart_conn(conn, cid, **kwargs):  # type: ignore[no-untyped-def]
        entered_write.set()
        assert release_write.wait(timeout=5), "test never released the restart's write"
        return real_record_restart_conn(conn, cid, **kwargs)

    movement._record_restart_conn = _blocking_record_restart_conn  # type: ignore[method-assign]

    restart_result: dict[str, int] = {}

    def _restart() -> None:
        resp = hub.client.post(f"/api/chunks/{chunk_id}/restart", json={})
        restart_result["status"] = resp.status_code

    restart_thread = threading.Thread(target=_restart)
    restart_thread.start()
    assert entered_write.wait(timeout=5), "restart never reached its (patched) write"

    claim_response: dict[str, object] = {}

    def _claim() -> None:
        resp = hub.client.post("/api/fleet/routes", json=_claim_body(chunk_id))
        claim_response["status"] = resp.status_code

    claim_thread = threading.Thread(target=_claim)
    claim_thread.start()
    claim_thread.join(timeout=0.3)
    assert claim_thread.is_alive(), "the claim completed while the restart still held the row lock — not atomic"

    release_write.set()
    restart_thread.join(timeout=5)
    claim_thread.join(timeout=5)

    assert restart_result["status"] == 202, restart_result
    # The restart's write landed first, under the lock — it minted no route of its own,
    # so the claim that resumes right after it still wins.
    assert claim_response["status"] == 201, claim_response
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "running"
