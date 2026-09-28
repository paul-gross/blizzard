"""The complete/claim race is atomic (component tier).

Mirrors ``tests/test_stop_claim_race.py``'s own interleaving pattern: ``CompleteService``
takes the row lock as the first statement of its own write (``bzh:store-exclusive-write``),
before it releases any live route — a claim landing on the same chunk can't interleave
with that check-then-act."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import cast

import pytest

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.chunks.route import IWriteChunkRouteRepository
from blizzard.hub.store.internal import chunk_lifecycle_store as chunk_lifecycle_store_module
from blizzard.tools.invariants import HubInvariants
from tests.support import build_hub, ingest, report_lease

pytestmark = pytest.mark.component


def _claim_body(chunk_id: str, runner: str = "r1") -> dict:
    return {"chunk_id": chunk_id, "runner_id": runner, "workspace_id": "w1", "environment_ids": ["env-a"]}


def _claim_and_lease(hub, chunk_id: str) -> None:  # type: ignore[no-untyped-def]
    resp = hub.client.post("/api/fleet/routes", json=_claim_body(chunk_id))
    assert resp.status_code == 201, resp.text
    report_lease(hub, chunk_id, epoch=1, seq=1)


def test_a_claim_blocks_while_a_complete_holds_the_row_lock_mid_write(tmp_path: Path) -> None:
    """Pause the completion mid-write (after its row lock, before its route release
    lands) and prove a concurrent claim on the same chunk blocks; once released, the
    claim's own fresh re-read finds the chunk done and refuses (409), never a route
    recorded against a terminal chunk."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])  # promote=True by default -> ready
    _claim_and_lease(hub, chunk_id)

    entered_write = threading.Event()
    release_write = threading.Event()
    real_route_of_conn = chunk_lifecycle_store_module.route_of_conn

    def _blocking_route_of_conn(conn, chunk_id):  # type: ignore[no-untyped-def]
        entered_write.set()
        assert release_write.wait(timeout=5), "test never released the completion's write"
        return real_route_of_conn(conn, chunk_id)

    monkeypatch_target = chunk_lifecycle_store_module
    monkeypatch_target.route_of_conn = _blocking_route_of_conn

    complete_result: dict[str, int] = {}

    def _complete() -> None:
        resp = hub.client.post(f"/api/chunks/{chunk_id}/complete", json={"by": "operator"})
        complete_result["status"] = resp.status_code

    complete_thread = threading.Thread(target=_complete)
    complete_thread.start()
    assert entered_write.wait(timeout=5), "completion never reached its (patched) write"

    claim_response: dict[str, object] = {}

    def _claim() -> None:
        resp = hub.client.post("/api/fleet/routes", json=_claim_body(chunk_id, runner="r2"))
        claim_response["status"] = resp.status_code

    claim_thread = threading.Thread(target=_claim)
    claim_thread.start()
    claim_thread.join(timeout=0.3)
    assert claim_thread.is_alive(), "the claim completed while the completion still held the row lock — not atomic"
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "running", (
        "the chunk must not already read as done while the completion still holds the lock"
    )

    release_write.set()
    complete_thread.join(timeout=5)
    claim_thread.join(timeout=5)

    monkeypatch_target.route_of_conn = real_route_of_conn

    assert complete_result["status"] == 202, complete_result
    # The completion's write landed first, under the lock — the claim that resumes right
    # after re-derives status fresh and refuses against the now-terminal chunk.
    assert claim_response["status"] == 409, claim_response
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"


def test_a_completion_still_releases_the_route_a_claim_won_the_lock_to_create(tmp_path: Path) -> None:
    """The reverse interleaving, behind the fix that stamps ``at`` from the store's own
    clock after the row lock (``bzh:store-exclusive-write``): pause the claim before its
    route fact lands, and prove a concurrent completion queued behind it still sees and
    releases that just-created route once it resumes — a release stamped before the wait
    it cleared would otherwise sort older than the route and never register as live."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])  # promote=True by default -> ready

    entered_record = threading.Event()
    release_record = threading.Event()
    writable_route = cast(IWriteChunkRouteRepository, hub.services.chunks.route)
    real_record_route_locked = writable_route.record_route_locked

    def _blocking_record_route_locked(handle, route, *, token_hash, at):  # type: ignore[no-untyped-def]
        entered_record.set()
        assert release_record.wait(timeout=5), "test never released the claim's route record"
        return real_record_route_locked(handle, route, token_hash=token_hash, at=at)

    writable_route.record_route_locked = _blocking_record_route_locked  # type: ignore[method-assign]

    claim_result: dict[str, int] = {}

    def _claim() -> None:
        claim_result["status"] = hub.client.post("/api/fleet/routes", json=_claim_body(chunk_id)).status_code

    claim_thread = threading.Thread(target=_claim)
    claim_thread.start()
    assert entered_record.wait(timeout=5), "claim never reached its (patched) route record"

    complete_result: dict[str, int] = {}

    def _complete() -> None:
        resp = hub.client.post(f"/api/chunks/{chunk_id}/complete", json={"by": "operator"})
        complete_result["status"] = resp.status_code

    complete_thread = threading.Thread(target=_complete)
    complete_thread.start()
    complete_thread.join(timeout=0.3)
    assert complete_thread.is_alive(), "the completion completed while the claim still held the shared lock — not atomic"

    release_record.set()
    claim_thread.join(timeout=5)
    complete_thread.join(timeout=5)

    assert claim_result["status"] == 201, claim_result
    assert complete_result["status"] == 202, complete_result
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"
    # The invariant this whole seam exists to keep: no chunk is both terminal and
    # carrying a route the release's timestamp lost the ordering race against.
    assert HubInvariants(create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")).run() == []
