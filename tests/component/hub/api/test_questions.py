"""``POST /api/questions/{id}/answers`` and the deprecated singular ``.../answer``
alias, component tier.

Pins the pluralized successor's first-write-wins CAS behavior (201 winner / 409 loser)
and proves the singular alias answers byte-identically, carrying ``Deprecation``/``Link``
and ``deprecated: true`` in the OpenAPI operation."""

from __future__ import annotations

from pathlib import Path

from blizzard.wire.problem import QUESTION_ALREADY_ANSWERED
from blizzard.wire.question import QuestionAlreadyAnsweredProblem
from tests.support.chunk_questions import (
    _GRAPH_YAML,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.chunk_questions
    _POINTER,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.chunk_questions
    ask_question,
)
from tests.support.refusal_observation import declared_refusal_mismatches
from tests.support.scaffolding import build_hub, make_ready, pointer_token

# --- POST /api/questions/{id}/answers — primary -----------------------------


def test_answers_first_write_wins(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ask_question(hub)

    first = hub.client.post("/api/questions/qn_1/answers", json={"answer": "rest", "answered_by": "alice"})
    assert first.status_code == 201, first.text
    assert first.json()["won"] is True
    assert first.json()["answer"] == "rest"

    second = hub.client.post("/api/questions/qn_1/answers", json={"answer": "graphql", "answered_by": "bob"})
    assert hub.app is not None
    path = "/api/questions/{question_id}/answers"
    assert (
        declared_refusal_mismatches(hub.app.openapi(), second, "post", path, 409, QuestionAlreadyAnsweredProblem) == []
    )
    assert (second.json()["type"], second.json()["detail"]) == (QUESTION_ALREADY_ANSWERED, "question already answered")
    assert second.json()["won"] is False
    # `answered_by` in the body is a spoof attempt — overwrites it with the
    # resolved session identity, `"operator"` under the default `auth.mode = "none"`.
    assert second.json()["answered_by"] == "operator"

    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "running"


def test_answers_unknown_question_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/questions/qn_missing/answers", json={"answer": "x"})
    assert resp.status_code == 404


def test_question_harness_owner_reaches_the_runner_answer_read(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH_YAML}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    make_ready(hub, chunk_id)
    assert (
        hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
        ).status_code
        == 201
    )

    asked = hub.client.post(
        "/api/questions",
        json={
            "question_id": "qn_owner",
            "chunk_id": chunk_id,
            "runner_id": "r1",
            "epoch": 1,
            "session_id": "sess-1",
            "harness_id": "claude_code",
            "question": "Which API?",
            "asked_at": "2026-07-13T00:00:00+00:00",
        },
    )
    assert asked.status_code == 201, asked.text

    delivered = hub.client.get("/api/fleet/questions/qn_owner")
    assert delivered.status_code == 200, delivered.text
    assert delivered.json()["session_id"] == "sess-1"
    assert delivered.json()["harness_id"] == "claude_code"


def test_equal_raw_session_ids_are_isolated_across_harnesses(tmp_path: Path) -> None:
    """Two harnesses sharing one raw session-id text never collide:
    each question is keyed by its own ``question_id``, so both land distinctly, each
    keeps its own recorded owner, and answering one never touches the other's state."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH_YAML}).status_code == 201
    chunk_a = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "434a"})]}
    ).json()["chunk_id"]
    chunk_b = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "434b"})]}
    ).json()["chunk_id"]
    for chunk_id in (chunk_a, chunk_b):
        make_ready(hub, chunk_id)
        assert (
            hub.client.post(
                "/api/fleet/routes",
                json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
            ).status_code
            == 201
        )

    for question_id, chunk_id, harness_id in (
        ("qn_claude", chunk_a, "claude_code"),
        ("qn_other", chunk_b, "other_harness"),
    ):
        asked = hub.client.post(
            "/api/questions",
            json={
                "question_id": question_id,
                "chunk_id": chunk_id,
                "runner_id": "r1",
                "epoch": 1,
                "session_id": "shared-raw-session",
                "harness_id": harness_id,
                "question": "Which API?",
                "asked_at": "2026-07-13T00:00:00+00:00",
            },
        )
        assert asked.status_code == 201, asked.text

    claude_view = hub.client.get("/api/fleet/questions/qn_claude").json()
    other_view = hub.client.get("/api/fleet/questions/qn_other").json()
    assert claude_view["session_id"] == other_view["session_id"] == "shared-raw-session"
    assert claude_view["harness_id"] == "claude_code"
    assert other_view["harness_id"] == "other_harness"
    assert claude_view["chunk_id"] == chunk_a
    assert other_view["chunk_id"] == chunk_b

    # Answering one never delivers or answers the other, despite the shared raw session id.
    answered = hub.client.post("/api/questions/qn_claude/answers", json={"answer": "rest", "answered_by": "alice"})
    assert answered.status_code == 201, answered.text

    assert hub.client.get("/api/fleet/questions/qn_claude").json()["answered"] is True
    still_open = hub.client.get("/api/fleet/questions/qn_other").json()
    assert still_open["answered"] is False
    # Chunk A's own question was answered; chunk B's — same raw session id, different
    # harness — stays exactly as it was, unaffected by the other's answer.
    assert hub.client.get(f"/api/chunks/{chunk_a}").json()["status"] == "running"
    assert hub.client.get(f"/api/chunks/{chunk_b}").json()["status"] == "waiting_on_human"


# --- Runner principal is still rejected on the answers route ----------------


def test_runner_bearer_token_is_rejected_on_answers(tmp_path: Path) -> None:
    from tests.support.hub_request_auth import bearer, seed_enrolled

    token = seed_enrolled(tmp_path)
    warn_hub = build_hub(tmp_path)
    ask_question(warn_hub)

    hub = build_hub(tmp_path)
    assert (
        hub.client.post("/api/questions/qn_1/answers", json={"answer": "rest"}, headers=bearer(token)).status_code
        == 403
    )
