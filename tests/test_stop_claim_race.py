"""The stop/claim race is atomic (component tier).

Mirrors ``tests/test_delete_claim_race.py``'s own interleaving pattern: ``StopService``
takes the row lock as the first statement of its own write (``bzh:store-exclusive-write``),
before it releases any live route — a claim landing on the same chunk can't interleave
with that check-then-act."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from blizzard.hub.store.internal import chunk_lifecycle_store as chunk_lifecycle_store_module
from tests.support import build_hub, ingest, report_lease

pytestmark = pytest.mark.component


def _claim_body(chunk_id: str, runner: str = "r1") -> dict:
    return {"chunk_id": chunk_id, "runner_id": runner, "workspace_id": "w1", "environment_ids": ["env-a"]}


def _claim_and_lease(hub, chunk_id: str) -> None:  # type: ignore[no-untyped-def]
    resp = hub.client.post("/api/fleet/routes", json=_claim_body(chunk_id))
    assert resp.status_code == 201, resp.text
    report_lease(hub, chunk_id, epoch=1, seq=1)


def test_a_claim_blocks_while_a_stop_holds_the_row_lock_mid_write(tmp_path: Path) -> None:
    """Pause the stop mid-write (after its row lock, before its route release lands) and prove a concurrent claim on
    the same chunk blocks; once released, the claim re-reads fresh and refuses (409) against the now-stopped chunk."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])  # promote=True by default -> ready
    _claim_and_lease(hub, chunk_id)

    entered_write = threading.Event()
    release_write = threading.Event()
    real_route_of_conn = chunk_lifecycle_store_module.route_of_conn

    def _blocking_route_of_conn(conn, chunk_id):  # type: ignore[no-untyped-def]
        entered_write.set()
        assert release_write.wait(timeout=5), "test never released the stop's write"
        return real_route_of_conn(conn, chunk_id)

    monkeypatch_target = chunk_lifecycle_store_module
    monkeypatch_target.route_of_conn = _blocking_route_of_conn

    stop_result: dict[str, int] = {}

    def _stop() -> None:
        resp = hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"})
        stop_result["status"] = resp.status_code

    stop_thread = threading.Thread(target=_stop)
    stop_thread.start()
    assert entered_write.wait(timeout=5), "stop never reached its (patched) write"

    claim_response: dict[str, object] = {}

    def _claim() -> None:
        resp = hub.client.post("/api/fleet/routes", json=_claim_body(chunk_id, runner="r2"))
        claim_response["status"] = resp.status_code

    claim_thread = threading.Thread(target=_claim)
    claim_thread.start()
    claim_thread.join(timeout=0.3)
    assert claim_thread.is_alive(), "the claim completed while the stop still held the row lock — not atomic"
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "running", (
        "the chunk must not already read as stopped while the stop still holds the lock"
    )

    release_write.set()
    stop_thread.join(timeout=5)
    claim_thread.join(timeout=5)

    monkeypatch_target.route_of_conn = real_route_of_conn

    assert stop_result["status"] == 202, stop_result
    # The stop's write landed first, under the lock — the claim that resumes right after
    # re-derives status fresh and refuses against the now-terminal chunk.
    assert claim_response["status"] == 409, claim_response
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "stopped"
