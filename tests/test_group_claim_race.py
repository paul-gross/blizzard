"""The group/claim race is atomic (component tier).

Mirrors ``tests/test_delete_claim_race.py``'s own interleaving pattern: ``GroupService``
holds the row lock over the survivor and every named merge id
(``bzh:store-exclusive-write``) across its whole fold, so a claim landing on the survivor
mid-fold can't interleave with the fold's own guard-check-then-write."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import cast

import pytest

from blizzard.hub.domain.chunk.ports.work_refs import IWriteChunkWorkRefsRepository
from tests.support import build_hub, ingest

pytestmark = pytest.mark.component


def _claim_body(chunk_id: str, runner: str = "r1") -> dict:
    return {"chunk_id": chunk_id, "runner_id": runner, "workspace_id": "w1", "environment_ids": ["env-a"]}


def test_a_claim_blocks_while_a_group_holds_the_row_lock_mid_fold(tmp_path: Path) -> None:
    """Pause the fold mid-write and prove a concurrent claim on the survivor blocks on
    the same row lock; once released, the claim lands against the now-folded survivor."""
    hub = build_hub(tmp_path)
    survivor_id = ingest(hub, [{"source": "default", "ref": "survivor"}])
    target_id = ingest(hub, [{"source": "default", "ref": "target"}])

    entered_write = threading.Event()
    release_write = threading.Event()
    work_refs = cast(IWriteChunkWorkRefsRepository, hub.services.chunks.work_refs)
    real_add_work_refs_locked = work_refs.add_work_refs_locked

    def _blocking_add_work_refs_locked(handle, chunk_id, pointers, *, at):  # type: ignore[no-untyped-def]
        entered_write.set()
        assert release_write.wait(timeout=5), "test never released the fold's write"
        return real_add_work_refs_locked(handle, chunk_id, pointers, at=at)

    work_refs.add_work_refs_locked = _blocking_add_work_refs_locked  # type: ignore[method-assign]

    group_result: dict[str, object] = {}

    def _group() -> None:
        group_result["result"] = hub.services.group.group(survivor_id, [target_id])

    group_thread = threading.Thread(target=_group)
    group_thread.start()
    assert entered_write.wait(timeout=5), "the group never reached its (patched) write"

    claim_response: dict[str, object] = {}

    def _claim() -> None:
        resp = hub.client.post("/api/fleet/routes", json=_claim_body(survivor_id))
        claim_response["status"] = resp.status_code

    claim_thread = threading.Thread(target=_claim)
    claim_thread.start()
    claim_thread.join(timeout=0.3)
    assert claim_thread.is_alive(), "the claim completed while the group still held the row lock — not atomic"

    release_write.set()
    group_thread.join(timeout=5)
    claim_thread.join(timeout=5)

    assert "result" in group_result, group_result
    # The fold's write landed first, under the lock — the claim that resumes right after
    # wins against the now-folded survivor.
    assert claim_response["status"] == 201, claim_response
    detail = hub.client.get(f"/api/chunks/{survivor_id}").json()
    assert detail["status"] == "running"
    assert hub.client.get(f"/api/chunks/{target_id}").status_code == 404
