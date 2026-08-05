"""The late-write fence after a detach (issue #38 acceptance criterion).

A detached runner's late completion is rejected by the lease floor and does not
resurrect the route. Detach relies on the existing epoch fence as-is — this test
proves that reliance holds, not the fence's own mechanics.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from tests.support import build_hub, pointer_token, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "38"}

# A gateless build -> deliver graph — the same minimal shape `tests/test_zombie_fence.py`
# and `tests/test_gates.py` already use for fence/detach coverage.
_PLAIN_YAML = """
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

_ARTIFACT = {
    "name": "acme/widget",
    "kind": "git_commit",
    "repo": "acme/widget",
    "branch_name": "b",
    "commit_hash": "late-c",
}


def _completion(node_id: str, *, epoch: int, runner_id: str) -> dict:
    return {
        "choice": "pass",
        "epoch": epoch,
        "runner_id": runner_id,
        "from_node_id": node_id,
        "artifacts": [_ARTIFACT],
    }


def test_a_detached_runners_late_completion_is_rejected_and_does_not_resurrect_the_route(
    tmp_path: Path,
) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _PLAIN_YAML}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

    # Runner A claims the chunk and mints its lease at epoch 1 — the initial floor.
    claim_a = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e1"]},
    )
    assert claim_a.status_code == 201, claim_a.text
    build_node_id = claim_a.json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1, runner_id="r1")

    # The operator detaches — one `route.released` fact; the chunk re-derives `ready`.
    # The lease floor stays epoch 1 — detach does not bump it (guardrail 3).
    hub.clock.advance(timedelta(seconds=1))
    detach = hub.client.post(f"/api/chunks/{chunk_id}/detach")
    assert detach.status_code == 202, detach.text
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"

    # Another runner claims the now-ready chunk and mints its OWN lease at epoch 2 —
    # this raises the lease floor; before it, A's epoch-1 completion was still current.
    hub.clock.advance(timedelta(seconds=1))
    claim_b = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r2", "workspace_id": "w2", "environment_ids": ["e2"]},
    )
    assert claim_b.status_code == 201, claim_b.text
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "running"
    report_lease(hub, chunk_id, epoch=2, seq=1, runner_id="r2")

    # Runner A's late completion arrives — its buffered fact, carrying the now-stale
    # epoch 1.
    late = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json=_completion(build_node_id, epoch=1, runner_id="r1"),
    )
    assert late.status_code == 200, late.text
    body = late.json()
    assert body["outcome"] == "failure"
    assert "stale epoch" in body["detail"]

    # The fence held: the chunk did not advance.
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "running"
    assert detail["current_node_id"] == build_node_id

    # The route was NOT resurrected: the chunk still belongs to runner B's fresh
    # route, and did not re-derive back to `ready`.
    assert detail["route"] is not None
    assert detail["route"]["runner_id"] == "r2"
    assert detail["status"] != "ready"

    # And the legitimate holder (fresh epoch) still delivers normally afterward — the
    # fence blocked only the zombie, not the live attempt.
    winner = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json=_completion(build_node_id, epoch=2, runner_id="r2"),
    )
    assert winner.json()["outcome"] == "hub_node_taken", winner.text
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"
