"""The ask/answer wire shapes ([ask-answer.md] / domain/questions.md).

The rendezvous spans the two daemons deliberately: the runner
forwards a ``question.asked`` up to the hub — as a batched ``POST /events`` fact and,
equivalently, the typed ``POST /questions`` route — where it becomes a durable row;
``POST /questions/{id}/answer`` writes the answer first-write-wins; and the runner
polls ``GET /questions/{id}`` to pick the answer up and resume the dormant session.
"""

from __future__ import annotations

from pydantic import BaseModel


class QuestionAsked(BaseModel):
    """A ``question.asked`` fact the runner forwards to the hub ([ask-answer.md]).

    ``question_id`` is runner-minted (``qn_<ulid>``) so the runner can poll the answer
    back by it; ``epoch`` is the parked lease's fence, ``session_id`` the dormant
    session to resume around the answer, and ``options`` the choices the board renders.
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
    """The body of ``POST /questions/{id}/answer`` — the human's answer ([ask-answer.md])."""

    answer: str
    answered_by: str = "operator"


class AnswerResult(BaseModel):
    """The answer write's outcome — first-write-wins CAS ([ask-answer.md]).

    ``won`` is True for the write that landed the row; the loser gets ``won=False`` with
    the **winning** row so it can be told who already answered (the 409 body)."""

    won: bool
    question_id: str
    answer: str
    answered_by: str
    answered_at: str


class QuestionView(BaseModel):
    """A question row with its derived answer state — the surfacing shape.

    Behind ``GET /questions`` (open only), ``GET /questions/{id}`` (the runner's answer
    poll), and the chunk detail's open-questions list. ``answered`` and the answer
    fields derive from the presence of the answer row."""

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
