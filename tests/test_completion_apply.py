"""Completion apply — the epoch fence, terminal rejection, and bad choices (component tier).

The happy path and idempotent replay are covered by ``test_delivery_loop``; this file
pins the rejection edges (``bzh:facts-not-status`` / D-007 / D-072).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub

pytestmark = pytest.mark.component

_POINTER = {"provider": "github", "url": "http://forge.local/repos/acme/widget/issues/9"}

# A build -> deliver graph named `default-delivery`, reused by name on ingest (D-081),
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


def _claimed(hub) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    assert hub.client.post("/api/graphs", json={"definition_yaml": _BUILD_DELIVER_YAML}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"pointers": [_POINTER]}).json()["chunk_id"]
    node_id = hub.client.post(
        "/api/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
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
    # The chunk never advanced and no artifact entered the store (D-007).
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


def test_completion_on_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post(
        "/api/chunks/ch_missing/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": "nd_x"},
    )
    assert resp.status_code == 404
