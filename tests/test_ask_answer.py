"""The ask/answer rendezvous at the hub (component tier) — MVP criterion 7.

Pins the hub half of the protocol ([ask-answer.md]) against a fully-wired store:

* a forwarded ``question.asked`` (both the batched ``POST /events`` path the runner
  uses and the typed ``POST /questions`` route) lands a durable row, and the chunk
  derives **waiting_on_human** with the question surfaced on its detail;
* the answer is **first-write-wins CAS** — a racing second answer loses with 409 and
  is told who already answered — and the winning row flips the chunk back to running;
* ``GET /questions`` lists only the open ones (the ``blizzard hub status`` surface).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub

pytestmark = pytest.mark.component

_POINTER = {"provider": "github", "url": "http://forge.local/repos/acme/widget/issues/7"}

_GRAPH_YAML = """
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
    retries:
      max: 2
      exhausted: escalate
  deliver:
    executor: hub
    mode: merge-to-main
"""


def _claim(hub) -> str:  # type: ignore[no-untyped-def]
    assert hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH_YAML}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"pointers": [_POINTER]}).json()["chunk_id"]
    claim = hub.client.post(
        "/api/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    return chunk_id


def _ask(hub, chunk_id: str, *, question_id: str = "qn_1", question: str = "Which API?") -> None:  # type: ignore[no-untyped-def]
    resp = hub.client.post(
        "/api/questions",
        json={
            "question_id": question_id,
            "chunk_id": chunk_id,
            "node_id": "nd_build",
            "session_id": "sess-1",
            "runner_id": "r1",
            "epoch": 1,
            "question": question,
            "options": ["rest", "graphql"],
            "asked_at": "2026-07-13T00:00:00+00:00",
        },
    )
    assert resp.status_code == 201, resp.text


def test_forwarded_question_parks_chunk_and_surfaces(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _claim(hub)
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "running"

    _ask(hub, chunk_id)

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "waiting_on_human"
    assert [q["question_id"] for q in detail["questions"]] == ["qn_1"]
    assert detail["questions"][0]["options"] == ["rest", "graphql"]

    # GET /questions is the fleet open-question surface (hub status).
    open_qs = hub.client.get("/api/questions").json()
    assert [q["question_id"] for q in open_qs] == ["qn_1"]

    # GET /questions/{id} is the runner's answer poll — open until answered.
    poll = hub.client.get("/api/questions/qn_1").json()
    assert poll["answered"] is False


def test_question_asked_via_events_batch_lands(tmp_path: Path) -> None:
    # The store-and-forward path the reconciliation loop actually uses.
    hub = build_hub(tmp_path)
    chunk_id = _claim(hub)
    resp = hub.client.post(
        "/api/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 5,
                    "kind": "question.asked",
                    "payload": {
                        "question_id": "qn_batch",
                        "chunk_id": chunk_id,
                        "node_id": "nd_build",
                        "session_id": "sess-1",
                        "epoch": 1,
                        "question": "batch?",
                        "options": [],
                        "asked_at": "2026-07-13T00:00:00+00:00",
                    },
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] == [5]
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "waiting_on_human"


def test_answer_first_write_wins_second_gets_409_with_winner(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _claim(hub)
    _ask(hub, chunk_id)

    first = hub.client.post("/api/questions/qn_1/answer", json={"answer": "rest", "answered_by": "alice"})
    assert first.status_code == 201, first.text
    assert first.json() == {
        "won": True,
        "question_id": "qn_1",
        "answer": "rest",
        "answered_by": "alice",
        "answered_at": first.json()["answered_at"],
    }

    # A racing second answer loses the CAS and is told who already answered.
    second = hub.client.post("/api/questions/qn_1/answer", json={"answer": "graphql", "answered_by": "bob"})
    assert second.status_code == 409, second.text
    body = second.json()
    assert body["won"] is False
    assert body["answered_by"] == "alice"
    assert body["answer"] == "rest"

    # The winning answer flips the chunk back out of waiting_on_human (D-004).
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "running"
    assert detail["questions"] == []
    assert hub.client.get("/api/questions").json() == []
    poll = hub.client.get("/api/questions/qn_1").json()
    assert poll["answered"] is True
    assert poll["answer"] == "rest"


def test_answer_delivered_fact_is_accepted(tmp_path: Path) -> None:
    # The runner reports answer.delivered up after resuming; the hub records it (board
    # detail) rather than rejecting an unknown kind.
    hub = build_hub(tmp_path)
    chunk_id = _claim(hub)
    _ask(hub, chunk_id)
    hub.client.post("/api/questions/qn_1/answer", json={"answer": "rest"})
    resp = hub.client.post(
        "/api/events",
        json={
            "runner_id": "r1",
            "facts": [{"seq": 9, "kind": "answer.delivered", "payload": {"chunk_id": chunk_id, "question_id": "qn_1"}}],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] == [9]
    assert resp.json()["rejected"] == []


def test_answer_unknown_question_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/questions/qn_missing/answer", json={"answer": "x"})
    assert resp.status_code == 404


def test_question_on_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post(
        "/api/questions",
        json={
            "question_id": "qn_x",
            "chunk_id": "ch_missing",
            "runner_id": "r1",
            "epoch": 1,
            "question": "?",
            "asked_at": "2026-07-13T00:00:00+00:00",
        },
    )
    assert resp.status_code == 404
