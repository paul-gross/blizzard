"""SSE emission across the fact lifecycle — asserted via TestClient stream reads.

Every hub fact that changes a chunk's derived state re-broadcasts a typed event on the
live stream: a forwarded ask emits ``question-asked``, an
answer ``question-answered``; a graph gate opening emits ``decision-opened`` and its
resolution ``decision-resolved``. Each test drives the wired hub, then reads the replayed
buffer off ``GET /api/events/stream`` — a real stream read, deterministic because the
replay tail is buffered before the read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub, emitted_events, pointer_token, report_lease

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
    "branch_name": "b",
    "commit_hash": "c",
}


def test_question_ask_and_answer_emit_typed_events(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]

    ask = hub.client.post(
        "/api/questions",
        json={
            "question_id": "qn_1",
            "chunk_id": chunk_id,
            "runner_id": "r1",
            "epoch": 1,
            "question": "Which library?",
            "options": ["a", "b"],
            "asked_at": "2026-07-13T00:00:00+00:00",
        },
    )
    assert ask.status_code == 201, ask.text
    answer = hub.client.post("/api/questions/qn_1/answer", json={"answer": "a", "answered_by": "op"})
    assert answer.status_code == 201, answer.text

    events = emitted_events(hub)
    types = [e["event"] for e in events]
    assert "question-asked" in types
    assert "question-answered" in types
    asked = next(e for e in events if e["event"] == "question-asked")
    assert "qn_1" in asked["data"] and chunk_id in asked["data"]


def test_decision_open_and_resolve_emit_typed_events(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _GATE_YAML})
    assert graph.status_code == 201, graph.text
    nodes = {n["name"]: n["node_id"] for n in graph.json()["nodes"]}
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]

    hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    report_lease(hub, chunk_id, epoch=1, seq=1)

    # build passes -> lands on the human gate -> the hub opens a decision (decision-opened).
    completion = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": 1,
            "runner_id": "r1",
            "from_node_id": nodes["build"],
            "artifacts": [_BUILD_ARTIFACT],
        },
    )
    assert completion.json()["outcome"] == "parked_at_gate", completion.text
    decision_id = hub.client.get(f"/api/chunks/{chunk_id}").json()["decision"]["decision_id"]

    resolve = hub.client.post(f"/api/decisions/{decision_id}/resolution", json={"choice": "approve"})
    assert resolve.status_code == 200, resolve.text

    events = emitted_events(hub)
    types = [e["event"] for e in events]
    assert "decision-opened" in types
    assert "decision-resolved" in types
    opened = next(e for e in events if e["event"] == "decision-opened")
    assert decision_id in opened["data"]
