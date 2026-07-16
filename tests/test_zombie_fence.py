"""Zombie fencing — a reaped lease cannot deliver (criterion 3, component tier).

A worker whose lease was reaped may still be alive and may still submit — the epoch
fence, not the kill, is what guarantees it cannot deliver (design/runner/loop.md,
D-007). When a reap requeues the chunk, the successor mints a **fresh epoch** and
reports it up (D-082/D-044). This test proves at the hub that the zombie's late (or
buffered-then-flushed) completion, carrying the old epoch, is rejected before any
write: it neither records a transition (the chunk does not advance) nor reaches the
merge queue (nothing lands at the forge) — and the legitimate successor still lands.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import FakeForge, build_hub, pointer_token, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "3"}

# A build -> deliver graph named `default-delivery`, reused by name on ingest (D-081),
# so the fence reaches the deliver hub node in one build pass — decoupled from the
# packaged default graph's build -> review -> deliver shape.
_BUILD_DELIVER_YAML = """
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
        pass:
          description: Complete and green.
          to: deliver
        fail:
          description: Incomplete.
          to: build
  deliver:
    executor: hub
    mode: merge-to-main
"""


def _mint_build_deliver_graph(hub) -> None:  # type: ignore[no-untyped-def]
    assert hub.client.post("/api/graphs", json={"definition_yaml": _BUILD_DELIVER_YAML}).status_code == 201


def _completion(node_id: str, *, epoch: int, choice: str = "pass") -> dict:
    return {
        "choice": choice,
        "epoch": epoch,
        "runner_id": "r1",
        "from_node_id": node_id,
        "artifacts": [
            {"name": "w", "kind": "git_commit", "repo": "acme/widget", "branch_name": "b", "commit_hash": "c"}
        ],
    }


def test_reaped_lease_completion_is_fenced_and_cannot_deliver(tmp_path: Path) -> None:
    forge = FakeForge()
    hub = build_hub(tmp_path, forge=forge)
    _mint_build_deliver_graph(hub)
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    build_node_id = hub.client.post(
        "/api/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]

    # The first lease (epoch 1) is minted, then reaped for a stall; the requeue mints a
    # fresh successor lease (epoch 2). Both mints are reported up through POST /events.
    report_lease(hub, chunk_id, epoch=1, seq=1)  # the lease that will be reaped
    report_lease(hub, chunk_id, epoch=2, seq=2)  # the successor's fresh epoch (D-082)

    # The zombie — the reaped worker — flushes its completion carrying the OLD epoch.
    zombie = hub.client.post(f"/api/chunks/{chunk_id}/completions", json=_completion(build_node_id, epoch=1))
    assert zombie.status_code == 200
    assert zombie.json()["outcome"] == "failure"
    assert "stale epoch" in zombie.json()["detail"]

    # It advanced nothing: the chunk is still running at the build node, and nothing
    # reached the merge queue (the forge saw no land).
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "running"
    assert detail["current_node_id"] == build_node_id
    assert forge.landed == []

    # The legitimate successor (fresh epoch) delivers normally — the fence blocks only
    # the zombie, not the live attempt.
    winner = hub.client.post(f"/api/chunks/{chunk_id}/completions", json=_completion(build_node_id, epoch=2))
    assert winner.json()["outcome"] == "hub_node_taken"
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"
    assert [r.commit_hash for r in forge.landed] == ["c"]


def test_zombie_completion_never_enters_merge_queue_even_if_it_races_first(tmp_path: Path) -> None:
    """Even arriving before the successor's completion, a stale-epoch submit is inert."""
    forge = FakeForge()
    hub = build_hub(tmp_path, forge=forge)
    _mint_build_deliver_graph(hub)
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    build_node_id = hub.client.post(
        "/api/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)
    report_lease(hub, chunk_id, epoch=2, seq=2)

    # Two flushes in a row: the zombie (epoch 1) then nothing else. It must not land.
    hub.client.post(f"/api/chunks/{chunk_id}/completions", json=_completion(build_node_id, epoch=1))
    hub.client.post(f"/api/chunks/{chunk_id}/completions", json=_completion(build_node_id, epoch=1))

    assert forge.landed == []
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "running"
