"""The ask/answer wire shapes.

A ``question.asked`` reaches the hub either as a batched ``POST /events`` fact or,
equivalently, through the typed ``POST /questions`` route, where it becomes a durable
row; ``POST /questions/{id}/answer`` writes the answer first-write-wins; ``GET
/questions/{id}`` reads it back.
"""

from __future__ import annotations

from pydantic import BaseModel


class QuestionAsked(BaseModel):
    """A ``question.asked`` fact the runner forwards to the hub.

    ``question_id`` is runner-minted (``qn_<ulid>``) so the runner can poll the answer
    back by it; ``epoch`` is the parked lease's fence, ``session_id`` the dormant
    session to resume around the answer, and ``options`` the offered choices.
    """

    question_id: str
    chunk_id: str
    node_id: str | None = None
    session_id: str | None = None
    runner_id: str
    epoch: int
    question: str
    options: list[str] = []
    asked_at: str  # ISO-8601 instant the ask was recorded (reap clock stops here)


class AnswerRequest(BaseModel):
    """The body of ``POST /questions/{id}/answer`` — the human's answer."""

    answer: str
    answered_by: str = "operator"


class AnswerResult(BaseModel):
    """The answer write's outcome — first-write-wins CAS.

    ``won`` is True for the write that landed the row; the loser gets ``won=False`` with
    the **winning** row so it can be told who already answered (the 409 body)."""

    won: bool
    question_id: str
    answer: str
    answered_by: str
    answered_at: str


class QuestionView(BaseModel):
    """A question row with its derived answer *and delivery* state — the surfacing shape.

    Behind ``GET /questions`` (open only), ``GET /questions/{id}``, and the chunk
    detail's questions list. ``answered`` and the answer fields derive from the presence
    of the answer row; ``delivered``/``delivered_at`` derive from the
    ``answer.delivered`` fact (issue #165)."""

    question_id: str
    chunk_id: str
    node_id: str | None = None
    session_id: str | None = None
    runner_id: str
    epoch: int
    question: str
    options: list[str] = []
    asked_at: str
    answered: bool = False
    answer: str | None = None
    answered_by: str | None = None
    answered_at: str | None = None
    delivered: bool = False
    delivered_at: str | None = None
