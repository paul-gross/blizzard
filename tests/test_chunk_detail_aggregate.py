"""The chunk detail carries the whole aggregate the board renders — component tier.

``GET /chunks/{id}`` is the board's chunk view and the envelope feed: it must carry the
derived status, the route, the PM pointers, the full transition history (with the
judgement choice on each edge), the inline artifact store (git-commit refs and asset
content), the open gate decision, and any escalation. This test drives a build→gate
scenario and asserts every piece is present — the additive completeness deliverable 4
guards.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub, pointer_token, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "7"}

_GATE_YAML = """
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
          description: Ship it.
          to: deliver
        reject:
          description: Send it back.
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

_BUILD_ARTIFACT = {
    "name": "acme/widget",
    "kind": "git_commit",
    "repo": "acme/widget",
    "branch_name": "feature/widget",
    "commit_hash": "abc123",
}


def test_detail_carries_the_full_aggregate(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _GATE_YAML})
    nodes = {n["name"]: n["node_id"] for n in graph.json()["nodes"]}
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]

    hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e1", "e2"]},
    )
    report_lease(hub, chunk_id, epoch=1, seq=1)
    hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": 1,
            "runner_id": "r1",
            "from_node_id": nodes["build"],
            "artifacts": [_BUILD_ARTIFACT],
        },
    )

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()

    # Derived status + fence.
    assert detail["status"] == "waiting_on_human"
    assert detail["latest_epoch"] == 1

    # Route (runner/workspace/envs) and PM pointers.
    assert detail["route"]["runner_id"] == "r1"
    assert detail["route"]["environment_ids"] == ["e1", "e2"]
    assert [p["ref"] for p in detail["pm_pointers"]] == [_POINTER["ref"]]

    # Board-legible identity: the current node's human name and the
    # pointer's `{source}#{number}` label are resolved server-side onto the detail.
    assert detail["current_node_id"] == nodes["approve-gate"]
    assert detail["current_node_name"] == "approve-gate"
    assert detail["pm_pointers"][0]["label"] == "default#7"

    # Full transition history with the judgement choice on the edge, and the
    # nodes' human graph names resolved onto each edge so the timeline reads build -> gate.
    assert len(detail["history"]) == 1
    step = detail["history"][0]
    assert step["from_node_id"] == nodes["build"]
    assert step["from_node_name"] == "build"
    assert step["to_node_id"] == nodes["approve-gate"]
    assert step["to_node_name"] == "approve-gate"
    assert step["choice_name"] == "pass"

    # Inline artifact store — the git-commit reference (the hub stores the pointer),
    # with the forge branch link derived from the chunk's issue-shaped pointer.
    assert len(detail["artifacts"]) == 1
    art = detail["artifacts"][0]
    assert art["kind"] == "git_commit"
    assert (art["repo"], art["branch_name"], art["commit_hash"]) == ("acme/widget", "feature/widget", "abc123")
    assert art["branch_url"] == "http://forge.local/acme/widget/tree/feature/widget"

    # The open gate decision with its choices.
    assert detail["decision"]["node_name"] == "approve-gate"
    assert {c["name"] for c in detail["decision"]["choices"]} == {"approve", "reject"}


def test_detail_carries_the_pinned_graphs_name_and_created_at(tmp_path: Path) -> None:
    """The board's compact-ref graph label (issue #102) needs the pinned graph's name and
    mint instant on ``ChunkDetail`` — populated from the ``Graph`` already loaded during
    detail assembly, so this asserts it matches what `GET /api/graphs` independently reports."""
    hub = build_hub(tmp_path)
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _GATE_YAML}).json()
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    summary = next(g for g in hub.client.get("/api/graphs").json() if g["graph_id"] == graph["graph_id"])

    assert detail["graph_id"] == graph["graph_id"]
    assert detail["graph_name"] == "default-delivery"
    assert detail["graph_name"] == summary["name"]
    assert detail["graph_created_at"] == summary["created_at"]
