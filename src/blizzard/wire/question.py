"""The ask/answer wire shapes: the ``question.asked`` fact, the answer request and its result, and the question
view."""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.wire.base import WireModel


class QuestionAsked(BaseModel):
    """A ``question.asked`` fact.

    ``question_id`` is runner-minted (``qn_<ulid>``); ``epoch`` is the parked lease's
    fence, ``session_id`` the dormant session, ``options`` the offered choices."""

    question_id: str
    chunk_id: str
    node_id: str | None = None
    session_id: str | None = None
    # Optional during the hub/runner skew window; a current runner supplies it for any session.
    harness_id: str | None = None
    runner_id: str
    epoch: int
    # The parked attempt's lease — matched against the epoch's owning lease when both are known.
    lease_id: str | None = None
    question: str
    options: list[str] = []
    asked_at: str  # ISO-8601 instant the ask was recorded


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


class QuestionView(WireModel):
    """A question row with its derived answer *and delivery* state.

    ``answered`` and the answer fields are set once an answer is recorded;
    ``delivered``/``delivered_at`` once that answer has been delivered."""

    question_id: str
    chunk_id: str
    node_id: str | None = None
    session_id: str | None = None
    harness_id: str | None = None
    runner_id: str
    #: The asking runner's name — not unique, never a key; ``None`` when the registry holds no such runner.
    runner_name: str | None = None
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
    #: A restart answered this question for the system, so its answer will never be delivered.
    superseded: bool = False
    #: The id of the chunk's project; absent only from a hub older than the project fields.
    project_id: str | None = None
    #: The project's current slug; absent only from a hub older than the project fields.
    project_slug: str | None = None
    #: The project's current display name; absent only from a hub older than the project fields.
    project_name: str | None = None
