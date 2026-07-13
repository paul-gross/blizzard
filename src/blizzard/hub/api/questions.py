"""Question routes — the hub half of the ask/answer rendezvous ([ask-answer.md]).

``POST /questions`` lands the durable question row a runner forwards (the chunk
derives ``waiting_on_human``); ``POST /questions/{id}/answer`` writes the answer
**first-write-wins** (201 for the winner, 409 carrying the winning answer for a racing
loser); ``GET /questions`` lists the open ones for ``blizzard hub status``; and
``GET /questions/{id}`` is the runner's answer poll before it resumes the dormant
session. Controllers stay read-only over the store and delegate the writes to
:class:`~blizzard.hub.domain.questions.QuestionService` (``bzh:controller-read-only``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.work import ChunkFacts, QuestionRow, derive_chunk_status
from blizzard.wire.question import AnswerRequest, AnswerResult, QuestionAsked, QuestionView

router = APIRouter(prefix="/api", tags=["questions"])


def question_view(row: QuestionRow) -> QuestionView:
    """Render a stored question row as its wire view — derived answer state and all."""
    return QuestionView(
        question_id=row.question_id,
        chunk_id=row.chunk_id,
        node_id=row.node_id,
        session_id=row.session_id,
        runner_id=row.runner_id,
        epoch=row.epoch,
        question=row.question,
        options=row.options,
        asked_at=row.asked_at.isoformat(),
        answered=row.answered,
        answer=row.answer,
        answered_by=row.answered_by,
        answered_at=row.answered_at.isoformat() if row.answered_at is not None else None,
    )


@router.post("/questions", status_code=status.HTTP_201_CREATED)
def ask_question(fact: QuestionAsked, services: Annotated[HubServices, Depends(get_services)]) -> dict[str, str]:
    """Land a ``question.asked`` row — the chunk parks ``waiting_on_human`` ([ask-answer.md])."""
    if services.chunks.get(fact.chunk_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {fact.chunk_id}")
    services.questions.record_asked(fact)
    _publish(services, fact.chunk_id)
    return {"question_id": fact.question_id}


@router.post("/questions/{question_id}/answer", response_model=AnswerResult, status_code=status.HTTP_201_CREATED)
def answer_question(
    question_id: str, request: AnswerRequest, services: Annotated[HubServices, Depends(get_services)]
) -> object:
    """Answer a question first-write-wins; 409 carries the winning answer ([ask-answer.md])."""
    if services.chunks.get_question(question_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown question {question_id}")
    outcome = services.questions.answer(question_id, answer=request.answer, answered_by=request.answered_by)
    result = AnswerResult(
        won=outcome.won,
        question_id=outcome.question_id,
        answer=outcome.answer,
        answered_by=outcome.answered_by,
        answered_at=outcome.answered_at.isoformat(),
    )
    if not outcome.won:
        # A racing second answer — the loser is told who already answered (D-045 kin).
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=result.model_dump())
    # The winning answer row alone flips the chunk out of waiting_on_human.
    winner = services.chunks.get_question(question_id)
    if winner is not None:
        _publish(services, winner.chunk_id)
    return result


@router.get("/questions", response_model=list[QuestionView])
def list_open_questions(services: Annotated[HubServices, Depends(get_services)]) -> list[QuestionView]:
    """Every open (unanswered) question across the fleet — the ``hub status`` surface."""
    return [question_view(row) for row in services.chunks.list_open_questions()]


@router.get("/questions/{question_id}", response_model=QuestionView)
def get_question(question_id: str, services: Annotated[HubServices, Depends(get_services)]) -> QuestionView:
    """One question with its derived answer state — the runner's answer poll (D-004)."""
    row = services.chunks.get_question(question_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown question {question_id}")
    return question_view(row)


def _publish(services: HubServices, chunk_id: str) -> None:
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    services.events.publish_chunk_changed(chunk_id, derive_chunk_status(facts).value)
