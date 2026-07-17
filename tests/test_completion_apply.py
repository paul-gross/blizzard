"""Completion apply — the epoch fence, terminal rejection, and bad choices (component tier).

The happy path and idempotent replay are covered by ``test_delivery_loop``; this file
pins the rejection edges (``bzh:facts-not-status``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub, pointer_token, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "9"}

# A build -> deliver graph named `default-delivery`, reused by name on ingest,
# so these apply-mechanics tests reach a terminal chunk in one build pass — decoupled
# from the packaged default graph's P7 build -> review -> deliver shape.
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


# A spike -> deliver graph: one read-only work node that `produces` an asset and no
# code, routing into the same hub deliver node a code chunk uses — a chunk whose whole
# purpose is a review or a spike simply ends with assets instead of branch pointers, and
# the graph still ends in a deliver node. It is the non-code terminal — MVP criterion
# 10's 2nd sentence — reached hermetically at the apply tier.
_SPIKE_DELIVER_YAML = """
name: default-delivery
entry: spike
nodes:
  spike:
    executor: runner
    prompt: |
      Investigate; write nothing.
    produces:
      - spike-notes
    judgement:
      prompt: |
        Record the finding.
      choices:
        complete:
          description: Investigation done; findings recorded.
          to: deliver
  deliver:
    executor: hub
    mode: merge-to-main
"""


def _claimed(hub, *, graph_yaml: str = _BUILD_DELIVER_YAML) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    """Mint the graph, claim a route, and report the runner-minted lease (epoch 1)."""
    assert hub.client.post("/api/graphs", json={"definition_yaml": graph_yaml}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    node_id = hub.client.post(
        "/api/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)  # the fence input the completion checks against
    return chunk_id, node_id


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


def test_stale_epoch_is_rejected_and_nothing_lands(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claimed(hub)

    resp = hub.client.post(f"/api/chunks/{chunk_id}/completions", json=_completion(node_id, epoch=99))
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "failure"
    # The chunk never advanced and no artifact entered the store.
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "running"
    assert hub.forge.landed == []


def test_completion_on_terminal_chunk_fails(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claimed(hub)
    hub.client.post(f"/api/chunks/{chunk_id}/completions", json=_completion(node_id, epoch=1))
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"

    # A fresh (non-replayed) completion on a done chunk is rejected as terminal. A
    # later epoch keeps it out of the idempotency replay path so the terminal guard fires.
    late = hub.client.post(f"/api/chunks/{chunk_id}/completions", json=_completion(node_id, epoch=2, choice="fail"))
    assert late.json()["outcome"] == "failure"


def test_unknown_choice_is_a_failure(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claimed(hub)
    resp = hub.client.post(f"/api/chunks/{chunk_id}/completions", json=_completion(node_id, epoch=1, choice="nope"))
    assert resp.json()["outcome"] == "failure"
    assert hub.forge.landed == []


def test_non_code_chunk_completes_with_only_asset_artifacts(tmp_path: Path) -> None:
    """A spike chunk carrying only an asset reaches ``done`` — no code lands (criterion 10, 2nd sentence).

    The sibling apply tests (and every other completing-chunk test) end in a git deliver
    that lands branch pointers; this pins the *non-code* half: a node-step that
    ``produces`` an asset and no git commit routes into the deliver node, which lands
    nothing (no forge call, no PR) yet still finalizes the chunk terminal — so the chunk
    completes carrying only its asset. The full-rails equivalent is
    ``tests/e2e/test_spike_terminal_e2e.py``; this is the always-run hermetic guard.
    """
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claimed(hub, graph_yaml=_SPIKE_DELIVER_YAML)

    completion = {
        "choice": "complete",
        "epoch": 1,
        "runner_id": "r1",
        "from_node_id": node_id,
        "artifacts": [{"name": "spike-notes", "kind": "asset", "content": "no change warranted"}],
    }
    resp = hub.client.post(f"/api/chunks/{chunk_id}/completions", json=completion)
    assert resp.status_code == 200

    # Fleet truth: the empty deliver still finalizes the chunk terminal.
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"
    # Git truth: a non-code chunk lands nothing — the forge was never asked to merge.
    assert hub.forge.landed == []
    # Hub-durable artifacts: exactly the asset, and no git-commit pointer.
    artifacts = hub.client.get(f"/api/chunks/{chunk_id}").json()["artifacts"]
    assert [a["kind"] for a in artifacts] == ["asset"], f"expected only an asset artifact, got: {artifacts}"
    assert artifacts[0]["name"] == "spike-notes"
    assert artifacts[0]["content"] == "no change warranted"


def test_completion_on_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post(
        "/api/chunks/ch_missing/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": "nd_x"},
    )
    assert resp.status_code == 404
