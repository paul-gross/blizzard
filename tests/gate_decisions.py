"""Helpers for the decisions api tests, shared with other test modules."""

from __future__ import annotations

from tests.support import HubHarness, pointer_token, report_lease

_POINTER = {"source": "default", "ref": "104"}


GATE_YAML = """
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
          to: approve-gate
        fail:
          description: Incomplete.
          to: build
  approve-gate:
    executor: runner
    judgement:
      by: human
      choices:
        approve:
          description: Ship it — proceed to delivery.
          to: deliver
        reject:
          description: Send it back to build.
          to: build
  deliver:
    executor: hub
    action: pass
    judgement:
      choices:
        success:
          description: Delivered.
          to: done
        failure:
          description: Failed to deliver.
          to: build
"""


_BUILD_ARTIFACT = {
    "name": "acme/widget",
    "kind": "git_commit",
    "repo": "acme/widget",
    "branch_name": "b",
    "commit_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
}


def _open_decision(hub: HubHarness) -> str:  # pyright: ignore[reportUnusedFunction]  # imported by the test modules that share it
    """Mint the gated graph, ingest+promote+claim+lease a chunk, and drive it to an
    open decision; return the decision id."""
    graph = hub.client.post("/api/graphs", json={"definition_yaml": GATE_YAML})
    assert graph.status_code == 201, graph.text
    build_node_id = next(n["node_id"] for n in graph.json()["nodes"] if n["name"] == "build")
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    report_lease(hub, chunk_id, epoch=1, seq=1)
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": 1,
            "runner_id": "r1",
            "from_node_id": build_node_id,
            "artifacts": [_BUILD_ARTIFACT],
        },
    )
    assert resp.json()["outcome"] == "parked_at_gate", resp.text
    decision = hub.client.get(f"/api/chunks/{chunk_id}").json()["decision"]
    assert decision is not None
    return str(decision["decision_id"])
