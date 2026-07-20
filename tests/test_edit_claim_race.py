"""The edit/claim race is atomic (issue #120, component tier).

Issue #120 widens ``EditService``'s admit set from ``not_ready`` to also admit
``ready`` — a promoted-but-unclaimed chunk. That opens the edit window onto the same
chunk a runner's claim (``POST /api/fleet/routes``) can land against concurrently: both
are now check-then-act sequences reading and then acting on whether the chunk has a
live route. An unguarded pair is a torn read — the edit's status check could pass just
before a claim lands, then write against a chunk that is now leased.

These tests **force** the interleaving rather than hoping a bare ``threading.Barrier``
happens to expose it: a patched store call pauses one side mid-critical-section (after
its own check, before its write lands), and the other side's HTTP call is proven to
*block* on the very same lock rather than slip in underneath it — the only way to prove
the two services share one lock rather than each holding a private one that just
happens not to collide in a given run. A closing repeated-trial test (mirroring
``test_claim_exactly_once.py``) then races many bare, un-instrumented requests through a
barrier and asserts the same invariant holds regardless of which side's OS thread wins.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import cast

import pytest

from blizzard.hub.domain.work import IWriteChunkRepository
from tests.support import HubHarness, build_hub, ingest

pytestmark = pytest.mark.component


def _writable(hub: HubHarness) -> IWriteChunkRepository:
    """A test-only cast: ``HubHarness.services.chunks`` is read-typed
    (``bzh:controller-read-only``), but the live object is always the write-capable
    :class:`~blizzard.hub.store.internal.chunk_store.ChunkStore` — these tests patch its
    write methods to force the exact interleaving the shared lock must serialize."""
    return cast(IWriteChunkRepository, hub.services.chunks)


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
    prove a concurrent claim cannot complete underneath it — it must block on the same
    lock. Once the edit releases, the claim proceeds and lands against the new graph,
    never a torn mix of the two."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])  # promote=True by default -> ready
    alt_graph_id = _mint_alt_graph(hub)

    entered_write = threading.Event()
    release_write = threading.Event()
    real_set_graph = _writable(hub).set_graph

    def _blocking_set_graph(cid: str, *, graph_id: str) -> None:
        entered_write.set()
        assert release_write.wait(timeout=5), "test never released the edit's write"
        real_set_graph(cid, graph_id=graph_id)

    _writable(hub).set_graph = _blocking_set_graph  # type: ignore[method-assign]

    edit_result: dict[str, int] = {}

    def _edit() -> None:
        edit_result["status"] = hub.client.post(
            f"/api/chunks/{chunk_id}/graph", json={"graph_id": alt_graph_id}
        ).status_code

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
    # The edit's write landed first (this is exactly that interleaving) — the
    # claim's own envelope resolution must agree with the persisted graph_id,
    # never build the runner's first node from the graph the edge resolved
    # before the edit repinned it.
    body = cast(dict, claim_response["body"])
    assert body["envelope"]["graph_id"] == alt_graph_id, body


def test_an_edit_is_refused_while_a_claim_holds_the_shared_lock_mid_route_creation(tmp_path: Path) -> None:
    """The reverse interleaving: force the claim to pause after it has confirmed no
    live route exists but before its route fact lands, and prove a concurrent edit
    blocks on the same lock rather than writing underneath it — surfacing only after
    the claim finishes, and then refused (409), never a graph repin against a chunk
    that is (from the edit's perspective, once it can finally check) already running."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "2"}])  # promote=True by default -> ready
    alt_graph_id = _mint_alt_graph(hub)
    original_graph_id = hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"]

    entered_record = threading.Event()
    release_record = threading.Event()
    real_record_route = _writable(hub).record_route

    def _blocking_record_route(route, *, token_hash, at):  # type: ignore[no-untyped-def]
        entered_record.set()
        assert release_record.wait(timeout=5), "test never released the claim's route record"
        real_record_route(route, token_hash=token_hash, at=at)

    _writable(hub).record_route = _blocking_record_route  # type: ignore[method-assign]

    claim_result: dict[str, int] = {}

    def _claim() -> None:
        claim_result["status"] = hub.client.post("/api/fleet/routes", json=_claim_body(chunk_id)).status_code

    claim_thread = threading.Thread(target=_claim)
    claim_thread.start()
    assert entered_record.wait(timeout=5), "claim never reached its (patched) route record"

    edit_result: dict[str, int] = {}

    def _edit() -> None:
        edit_result["status"] = hub.client.post(
            f"/api/chunks/{chunk_id}/graph", json={"graph_id": alt_graph_id}
        ).status_code

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
    """Many chunks, each raced by a bare (un-instrumented) edit and claim released
    together through a barrier — whichever side's OS thread wins the shared lock, the
    result is always one of the two atomic outcomes, never a chunk left claimed with an
    edit that also silently landed against a graph the running route never saw."""
    hub = build_hub(tmp_path)
    alt_graph_id = _mint_alt_graph(hub)
    for i in range(8):
        chunk_id = ingest(hub, [{"source": "default", "ref": str(200 + i)}])
        original_graph_id = hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"]
        start = threading.Barrier(2)
        results: dict[str, object] = {}

        def _edit(cid: str = chunk_id, barrier: threading.Barrier = start, sink: dict[str, object] = results) -> None:
            barrier.wait()
            sink["edit"] = hub.client.post(f"/api/chunks/{cid}/graph", json={"graph_id": alt_graph_id}).status_code

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
