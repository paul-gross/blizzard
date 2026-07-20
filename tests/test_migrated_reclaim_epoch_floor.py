"""A migrated chunk reclaimed by a fresh runner mints above the hub epoch floor (issue #112).

A cross-graph migration (#90) re-pins a chunk and re-queues it ``ready`` for a **fresh
claim**, possibly by a runner that never drove it. That runner's runner-local epoch floor
(``store.latest_epoch``) is 0, while the chunk's hub-side history — append-only ``lease.minted``
facts spanning the *source* graph — carries epochs > 0. Before #112 the fresh runner minted
``local + 1 == 1``, at or below the hub's latest epoch, and every state-advancing completion
then bounced off the hub's stale-epoch fence (``stale epoch X; chunk is at Y``) — the chunk
wedged. The fix seeds the mint floor from ``max(local, envelope.epoch)``, where
``envelope.epoch`` is the hub's own ``latest_epoch(facts)`` carried on the claim response
(``bzh:epoch-fencing``), so a freshly-claimed migrated chunk always mints strictly-higher
epochs.

Driven end to end at the component tier over the **real** hub app and the **real** FILL step:
``HttpHubClient`` (the production ``IHubClient`` adapter the daemon runs) wraps the hub's own
``TestClient`` (itself an ``httpx.Client``) — the same real-seam shape ``test_hub_runner_seam.py``
and ``test_detach_late_write_fence.py`` use, no fake hub. Asserts the hub-supplied floor on the
wire (AC #1), the strictly-higher mint from a fresh runner store (AC #2/#3), and the preserved
late-write fence (AC #4).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.context import LoopConfig, LoopContext
from blizzard.runner.loop.internal.http_hub import HttpHubClient
from blizzard.runner.loop.steps import fill, flush_outbound
from tests.runner_fakes import FakeHarness, FakeProbe, FakeProvider, FakeWorktreeGit, make_store
from tests.support import build_hub, ingest, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "112"}
_HANDLE = WorkerHandle(session_id="sess-fresh", pid=200, process_start_time="start-200")

# The SOURCE graph ingest pins — named after the packaged default (`default-delivery`) so
# `ensure_default` resolves the ingested chunk to *this* graph — whose single `build` node
# migrates the chunk to `triage-delivery` rather than transitioning in place.
_SOURCE_YAML = """
name: default-delivery
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
        migrate:
          description: Hand the chunk to the triage-delivery graph.
          to: graph:triage-delivery
"""

# The TARGET graph a migration re-pins onto. Its own `build` name-matches the source's, so
# the migration lands the re-queued chunk on it (name-match-else-entry, #90).
_TARGET_YAML = """
name: triage-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change under the new graph.
    judgement:
      prompt: |
        Assess the build.
      choices:
        pass:
          description: Complete and green.
          to: done
"""


def test_migrated_chunk_reclaimed_by_a_fresh_runner_mints_above_the_hub_floor(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    # Mint the target first, so the source's cross-graph choice resolves at mint time.
    target = hub.client.post("/api/graphs", json={"definition_yaml": _TARGET_YAML})
    assert target.status_code == 201, target.text
    target_graph_id = target.json()["graph_id"]
    assert hub.client.post("/api/graphs", json={"definition_yaml": _SOURCE_YAML}).status_code == 201
    chunk_id = ingest(hub, [_POINTER])  # pins to `default-delivery` (the source above), promoted

    # --- Runner A drives the chunk through the SOURCE graph and migrates it -------------------
    claim_a = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e1"]},
    )
    assert claim_a.status_code == 201, claim_a.text
    source_build_id = claim_a.json()["envelope"]["node"]["node_id"]

    # Two spawns of `build` (a retry re-mint) seed the hub history at epoch 1 then 2 — the
    # prior-graph epochs a migration carries forward. N is the chunk's hub-side latest epoch.
    n = 2
    report_lease(hub, chunk_id, epoch=1, seq=1, runner_id="r1")
    report_lease(hub, chunk_id, epoch=n, seq=2, runner_id="r1")

    migrate = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "migrate", "epoch": n, "runner_id": "r1", "from_node_id": source_build_id, "artifacts": []},
    )
    assert migrate.status_code == 200, migrate.text
    assert migrate.json()["outcome"] == "migrated", migrate.text
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == target_graph_id, "the chunk was not re-pinned to the target graph"
    assert detail["status"] == "ready", "the migration did not re-queue the chunk for a fresh claim"

    # AC #1: the hub-supplied floor is on the claim/envelope wire — the hub's latest epoch
    # (N) carried across the migration, even though the target graph has minted no lease.
    assert hub.client.get(f"/api/fleet/chunks/{chunk_id}/envelope").json()["epoch"] == n

    # --- A FRESH runner (r2, empty local store) reclaims via a REAL FILL tick -----------------
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    assert store.latest_epoch(chunk_id) == 0, "the fresh runner store must carry no local history"
    provider = FakeProvider({"e9": "/ws/e9"})
    ctx = LoopContext(
        store=store,
        clock=hub.clock,
        hub=HttpHubClient(hub.client),
        provider=provider,
        harness=FakeHarness(handle=_HANDLE, verdict=None),
        process=FakeProbe(alive={(200, "start-200")}),
        worktree_git=FakeWorktreeGit(),
        config=LoopConfig(runner_id="r2", workspace_id="w2", max_agents=1),
    )
    fill(ctx)

    # AC #2/#3: the fresh runner had NO local history (floor 0) yet minted strictly above the
    # hub floor — `max(0, N) + 1 == N + 1`, not the pre-#112 local-only `0 + 1 == 1`.
    lease = store.active_lease_for_chunk(chunk_id)
    assert lease is not None, "the FILL tick minted no lease for the reclaimed chunk"
    assert lease.epoch == n + 1, f"expected mint at hub-floor+1 ({n + 1}), got {lease.epoch}"
    assert store.latest_epoch(chunk_id) == n + 1

    # Report the freshly-minted lease up through the real store-and-forward buffer, so the hub
    # learns the raised floor (N+1) the fence below consumes.
    flush_outbound(ctx)

    # AC #4: the late-write fence is preserved across the migration-reclaim path. A completion
    # at the OLD epoch (N) — now below the fresh lease's floor — is rejected. It rides the
    # target `build` node the fresh runner is on, so it reaches the epoch fence itself (a
    # source-graph node id would fail the target-graph membership check first).
    late = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": n, "runner_id": "r2", "from_node_id": lease.node_id, "artifacts": []},
    )
    assert late.status_code == 200, late.text
    body = late.json()
    assert body["outcome"] == "failure", body
    assert "stale epoch" in body["detail"], body

    # Nothing was resurrected: the chunk stays with runner r2's fresh route, never re-derived
    # back to `ready` by the stale write.
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "running"
    assert detail["route"] is not None and detail["route"]["runner_id"] == "r2"
