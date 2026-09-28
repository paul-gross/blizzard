"""The stop/claim race is atomic (component tier).

Mirrors ``tests/test_delete_claim_race.py``'s own interleaving pattern: ``StopService``
takes the row lock as the first statement of its own write (``bzh:store-exclusive-write``),
before it releases any live route — a claim landing on the same chunk can't interleave
with that check-then-act."""

from __future__ import annotations

import threading
from datetime import timedelta
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


def test_a_stop_still_releases_the_route_a_claim_won_the_lock_to_create(tmp_path: Path) -> None:
    """The reverse interleaving, behind the fix that stamps ``at`` from the store's own
    clock after the row lock (``bzh:store-exclusive-write``): pause the claim before its
    route fact lands, and prove a concurrent stop queued behind it still sees and
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

    stop_result: dict[str, int] = {}

    def _stop() -> None:
        resp = hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"})
        stop_result["status"] = resp.status_code

    stop_thread = threading.Thread(target=_stop)
    stop_thread.start()
    stop_thread.join(timeout=0.3)
    assert stop_thread.is_alive(), "the stop completed while the claim still held the shared lock — not atomic"

    # Advance the clock while the stop is still blocked — a stale `at` computed before the
    # wait would read this test's earlier instant; only a release stamped after the wait
    # clears reads the advanced one, which the final assertion below checks for.
    hub.clock.advance(timedelta(seconds=1))
    release_record.set()
    claim_thread.join(timeout=5)
    stop_thread.join(timeout=5)

    assert claim_result["status"] == 201, claim_result
    assert stop_result["status"] == 202, stop_result
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "stopped"
    # The invariant this whole seam exists to keep: no chunk is both terminal and
    # carrying a route the release's timestamp lost the ordering race against.
    assert HubInvariants(create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")).run() == []

    # Pin the mechanism directly: the release is stamped from the post-advance instant, not
    # a stale pre-wait one — a stale `at` would equal the route's own `created_at` instead
    # (this test's single un-advanced instant before the `advance()` above) and would sort
    # no later than it, which is exactly the ordering `RouteHistory.newest` tie-breaks on.
    facts = hub.services.chunks.facts.load_facts(chunk_id)
    assert facts is not None
    assert len(facts.routes_created) == 1
    assert len(facts.routes_released) == 1
    created_at = facts.routes_created[0].created_at
    released_at = facts.routes_released[0].released_at
    assert released_at > created_at, (created_at, released_at)
    assert released_at == hub.clock.now()
