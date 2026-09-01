"""The edit/claim race is atomic (issue #120, component tier).

Issue #120 widens ``EditService``'s admit set to also admit ``ready``, opening a window
where a runner's claim lands against the same chunk concurrently — an unguarded pair is
a torn read. These tests force the interleaving with a patched store call.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import cast

import pytest

from blizzard.hub.domain.chunks.record import IWriteChunkRecordRepository
from blizzard.hub.domain.chunks.route import IWriteChunkRouteRepository
from tests.support import HubHarness, build_hub, ingest

pytestmark = pytest.mark.component


def _writable_record(hub: HubHarness) -> IWriteChunkRecordRepository:
    """These tests patch the chunk-record store's write methods to force the exact
    interleaving the shared lock must serialize."""
    return hub.services.chunks.record


def _writable_route(hub: HubHarness) -> IWriteChunkRouteRepository:
    """Same as :func:`_writable_record`, for the chunk-route seam."""
    return hub.services.chunks.route


_ALT_YAML = """
name: alt-graph
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    judgement:
      prompt: |
        Assess the build.
      choices:
        pass:
          description: Complete and green.
          to: deliver
        fail:
          description: Incomplete.
          to: build
  deliver:
    executor: hub
    run:
      - command: "true"
    judgement:
      choices:
        success:
          description: Delivered.
          to: done
        failure:
          description: Failed to deliver.
          to: build
"""


def _mint_alt_graph(hub) -> str:  # type: ignore[no-untyped-def]
    resp = hub.client.post("/api/graphs", json={"definition_yaml": _ALT_YAML})
    assert resp.status_code == 201, resp.text
    return resp.json()["graph_id"]


def _claim_body(chunk_id: str, runner: str = "r1") -> dict:
    return {"chunk_id": chunk_id, "runner_id": runner, "workspace_id": "w1", "environment_ids": [f"env-{runner}"]}


def test_a_claim_blocks_while_an_edit_holds_the_shared_lock_mid_write(tmp_path: Path) -> None:
    """Force the edit to pause after its status check but before its write lands, and
    prove a concurrent claim blocks on the same lock rather than completing underneath
    it — once released, the claim lands against the new graph, never a torn mix."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])  # promote=True by default -> ready
    alt_graph_id = _mint_alt_graph(hub)

    entered_write = threading.Event()
    release_write = threading.Event()
    real_set_graph = _writable_record(hub).set_graph

    def _blocking_set_graph(cid: str, *, graph_id: str) -> None:
        entered_write.set()
        assert release_write.wait(timeout=5), "test never released the edit's write"
        real_set_graph(cid, graph_id=graph_id)

    _writable_record(hub).set_graph = _blocking_set_graph  # type: ignore[method-assign]

    edit_result: dict[str, int] = {}

    def _edit() -> None:
        edit_result["status"] = hub.client.patch(f"/api/chunks/{chunk_id}", json={"graph_id": alt_graph_id}).status_code

    edit_thread = threading.Thread(target=_edit)
    edit_thread.start()
    assert entered_write.wait(timeout=5), "edit never reached its (patched) write"

    claim_response: dict[str, object] = {}

    def _claim() -> None:
        resp = hub.client.post("/api/fleet/routes", json=_claim_body(chunk_id))
        claim_response["status"] = resp.status_code
        claim_response["body"] = resp.json()

    claim_thread = threading.Thread(target=_claim)
    claim_thread.start()
    claim_thread.join(timeout=0.3)
    assert claim_thread.is_alive(), "the claim completed while the edit still held the shared lock — not atomic"
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready", (
        "the chunk must not already show running while the edit still holds the lock"
    )

    release_write.set()
    edit_thread.join(timeout=5)
    claim_thread.join(timeout=5)

    assert edit_result["status"] == 202, edit_result
    assert claim_response["status"] == 201, claim_response
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == alt_graph_id
    assert detail["status"] == "running"
    # The edit's write landed first — the claim's envelope resolution must agree with
    # the persisted graph_id, never the graph resolved before the repin.
    body = cast(dict, claim_response["body"])
    assert body["envelope"]["graph_id"] == alt_graph_id, body


def test_an_edit_is_refused_while_a_claim_holds_the_shared_lock_mid_route_creation(tmp_path: Path) -> None:
    """The reverse interleaving: force the claim to pause before its route fact lands,
    and prove a concurrent edit blocks on the same lock rather than writing underneath
    it — surfacing only after, and then refused (409) against the now-running chunk."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "2"}])  # promote=True by default -> ready
    alt_graph_id = _mint_alt_graph(hub)
    original_graph_id = hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"]

    entered_record = threading.Event()
    release_record = threading.Event()
    real_record_route = _writable_route(hub).record_route

    def _blocking_record_route(route, *, token_hash, at):  # type: ignore[no-untyped-def]
        entered_record.set()
        assert release_record.wait(timeout=5), "test never released the claim's route record"
        real_record_route(route, token_hash=token_hash, at=at)

    _writable_route(hub).record_route = _blocking_record_route  # type: ignore[method-assign]

    claim_result: dict[str, int] = {}

    def _claim() -> None:
        claim_result["status"] = hub.client.post("/api/fleet/routes", json=_claim_body(chunk_id)).status_code

    claim_thread = threading.Thread(target=_claim)
    claim_thread.start()
    assert entered_record.wait(timeout=5), "claim never reached its (patched) route record"

    edit_result: dict[str, int] = {}

    def _edit() -> None:
        edit_result["status"] = hub.client.patch(f"/api/chunks/{chunk_id}", json={"graph_id": alt_graph_id}).status_code

    edit_thread = threading.Thread(target=_edit)
    edit_thread.start()
    edit_thread.join(timeout=0.3)
    assert edit_thread.is_alive(), "the edit completed while the claim still held the shared lock — not atomic"
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"] == original_graph_id, (
        "the graph must not already be repinned while the claim still holds the lock"
    )

    release_record.set()
    claim_thread.join(timeout=5)
    edit_thread.join(timeout=5)

    assert claim_result["status"] == 201, claim_result
    assert edit_result["status"] == 409, edit_result
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == original_graph_id
    assert detail["status"] == "running"


def test_repeated_edit_claim_races_never_yield_a_torn_graph(tmp_path: Path) -> None:
    """Many chunks, each raced by a bare edit and claim released together through a
    barrier — whichever side wins the shared lock, the result is always one of the two
    atomic outcomes, never a torn claim-plus-silent-repin."""
    hub = build_hub(tmp_path)
    alt_graph_id = _mint_alt_graph(hub)
    for i in range(8):
        chunk_id = ingest(hub, [{"source": "default", "ref": str(200 + i)}])
        original_graph_id = hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"]
        start = threading.Barrier(2)
        results: dict[str, object] = {}

        def _edit(cid: str = chunk_id, barrier: threading.Barrier = start, sink: dict[str, object] = results) -> None:
            barrier.wait()
            sink["edit"] = hub.client.patch(f"/api/chunks/{cid}", json={"graph_id": alt_graph_id}).status_code

        def _claim(
            cid: str = chunk_id, barrier: threading.Barrier = start, sink: dict[str, object] = results, n: int = i
        ) -> None:
            barrier.wait()
            resp = hub.client.post("/api/fleet/routes", json=_claim_body(cid, runner=f"r{n}"))
            sink["claim"] = resp.status_code
            sink["claim_body"] = resp.json()

        threads = [threading.Thread(target=_edit), threading.Thread(target=_claim)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results["claim"] == 201, f"chunk {i}: {results}"
        assert results["edit"] in (202, 409), f"chunk {i}: {results}"
        detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
        assert detail["status"] == "running"
        envelope_graph_id = cast(dict, results["claim_body"])["envelope"]["graph_id"]
        if results["edit"] == 202:
            # The edit's write landed before the claim locked in — the running route's
            # own envelope resolution and the persisted graph_id agree on the new graph.
            assert detail["graph_id"] == alt_graph_id, f"chunk {i}: {detail}"
        else:
            # The claim locked in first — the edit saw the live route and was refused,
            # so the original graph is untouched.
            assert detail["graph_id"] == original_graph_id, f"chunk {i}: {detail}"
        # Whichever side won, the claim's own envelope must agree with the
        # persisted graph_id it observed — never a torn read of the two.
        assert envelope_graph_id == detail["graph_id"], f"chunk {i}: {results} {detail}"
