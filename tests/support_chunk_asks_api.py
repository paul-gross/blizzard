"""Shared fixtures for the lease asks route tests (``tests/unit/runner/api/test_chunk_asks.py``, ``tests/component/runner/api/test_chunk_asks.py``)."""

from __future__ import annotations

from typing import TypedDict, Unpack

CHUNK = "ch_1"


class AnswerFields(TypedDict, total=False):
    options: list[str]
    answered: bool
    answer: str
    answered_by: str
    answered_at: str


def question_row(
    question_id: str, node_id: str | None, asked_at: str, **extra: Unpack[AnswerFields]
) -> dict[str, object]:
    return {
        "question_id": question_id,
        "chunk_id": CHUNK,
        "node_id": node_id,
        "runner_id": "r1",
        "epoch": 1,
        "question": f"q {question_id}",
        "asked_at": asked_at,
        **extra,
    }


# Deliberately out of oldest-first source order; a detail without ``history`` or ``migrations``.
DETAIL: dict[str, object] = {
    "chunk_id": CHUNK,
    "current_node_id": "nd_build",
    "current_node_name": "build",
    "questions": [
        question_row(
            "q2",
            "nd_ghost",
            "2026-07-21T11:00:00+00:00",
            answered=True,
            answer="no answer applies",
            answered_by="restart",
            answered_at="2026-07-21T11:30:00+00:00",
        ),
        question_row(
            "q1",
            "nd_build",
            "2026-07-21T10:00:00+00:00",
            options=["a", "b"],
            answered=True,
            answer="a",
            answered_by="paul",
            answered_at="2026-07-21T10:30:00+00:00",
        ),
        question_row("q3", "nd_build", "2026-07-21T12:00:00+00:00"),
    ],
}
