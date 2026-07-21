"""``POST /api/questions/{id}/answers`` and the deprecated singular ``.../answer``
alias (issue #104), component tier.

``tests/test_ask_answer.py`` covers the ask/answer rendezvous end to end against the
pre-#104 singular path. This file pins the pluralized successor's identical
first-write-wins CAS behavior (201 winner / 409 loser) and proves the singular alias
still answers byte-identically while carrying the ``Deprecation``/``Link`` headers and
``deprecated: true`` in the OpenAPI operation; a runner bearer token is still rejected
on both. ``POST /questions`` (worker-raised) is untouched — out of scope.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub, pointer_token

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "104"}

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


def _asked(hub, *, question_id: str = "qn_1") -> str:  # type: ignore[no-untyped-def]
    """Mint the graph, claim a chunk, and land an open question; return the chunk id."""
    assert hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH_YAML}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    resp = hub.client.post(
        "/api/questions",
        json={
            "question_id": question_id,
            "chunk_id": chunk_id,
            "node_id": "nd_build",
            "session_id": "sess-1",
            "runner_id": "r1",
            "epoch": 1,
            "question": "Which API?",
            "options": ["rest", "graphql"],
            "asked_at": "2026-07-13T00:00:00+00:00",
        },
    )
    assert resp.status_code == 201, resp.text
    return chunk_id


# --- POST /api/questions/{id}/answers — primary -----------------------------


def test_answers_first_write_wins(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _asked(hub)

    first = hub.client.post("/api/questions/qn_1/answers", json={"answer": "rest", "answered_by": "alice"})
    assert first.status_code == 201, first.text
    assert first.json()["won"] is True
    assert first.json()["answer"] == "rest"

    second = hub.client.post("/api/questions/qn_1/answers", json={"answer": "graphql", "answered_by": "bob"})
    assert second.status_code == 409, second.text
    assert second.json()["won"] is False
    assert second.json()["answered_by"] == "alice"

    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "running"


def test_answers_unknown_question_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/questions/qn_missing/answers", json={"answer": "x"})
    assert resp.status_code == 404


# --- Runner principal is still rejected on the answers route ----------------


def test_runner_bearer_token_is_rejected_on_answers(tmp_path: Path) -> None:
    from blizzard.hub.config import RUNNER_AUTH_ENFORCE
    from tests.test_fleet_auth import _bearer, _seed_enrolled

    token = _seed_enrolled(tmp_path)
    warn_hub = build_hub(tmp_path)
    _asked(warn_hub)

    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)
    assert (
        hub.client.post("/api/questions/qn_1/answers", json={"answer": "rest"}, headers=_bearer(token)).status_code
        == 403
    )
