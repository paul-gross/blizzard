"""Question routes — the anonymous **operator** half of the ask/answer rendezvous (#87).

``POST /questions`` lands the durable question row (the chunk derives
``waiting_on_human``); ``POST /questions/{id}/answers`` writes **first-write-wins**
(201 for the winner, 409 carrying the winning answer for a racing loser).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import FLEET_VIEW, QUESTION_ANSWER
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import chunk_events
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, resolved_username
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.work import QuestionRow
from blizzard.wire.question import AnswerRequest, AnswerResult, QuestionAsked, QuestionView

router = APIRouter(prefix="/api", tags=["questions"], dependencies=[Depends(reject_runner_principal)])


def question_view(row: QuestionRow) -> QuestionView:
    """Render a stored question row as its wire view — derived answer + delivery state."""
    return QuestionView(
        question_id=row.question_id,
        chunk_id=row.chunk_id,
        node_id=row.node_id,
        session_id=row.session_id,
        runner_id=row.runner_id,
        epoch=row.epoch,
        question=row.question,
        options=row.options,
        asked_at=iso_utc(row.asked_at),
        answered=row.answered,
        answer=row.answer,
        answered_by=row.answered_by,
        answered_at=iso_utc(row.answered_at) if row.answered_at is not None else None,
        delivered=row.delivered,
        delivered_at=iso_utc(row.delivered_at) if row.delivered_at is not None else None,
    )


@router.post("/questions", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require(QUESTION_ANSWER))])
def ask_question(fact: QuestionAsked, services: Annotated[HubServices, Depends(get_services)]) -> dict[str, str]:
    """Land a ``question.asked`` row — the chunk parks ``waiting_on_human``."""
    if services.chunks.get(fact.chunk_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {fact.chunk_id}")
    change = chunk_events.ChunkChanged.before(services, fact.chunk_id)
    services.questions.record_asked(fact)
    key = f"questions:{fact.question_id}"
    services.events.publish_question_asked(fact.chunk_id, fact.question_id, key=key)
    change.publish(cause="question-asked", key=key)
    return {"question_id": fact.question_id}


@router.post(
    "/questions/{question_id}/answers",
    response_model=AnswerResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(QUESTION_ANSWER))],
)
def answer_question(
    question_id: str,
    request: AnswerRequest,
    http_request: Request,
    services: Annotated[HubServices, Depends(get_services)],
) -> object:
    """Answer a question first-write-wins; 409 carries the winning answer.

    ``answered_by`` is taken from the authenticated session identity, never the request
    body's ``answered_by`` field — a spoofed value there is silently ignored (issue #91)."""
    pre_answer = services.chunks.get_question(question_id)
    if pre_answer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown question {question_id}")
    change = chunk_events.ChunkChanged.before(services, pre_answer.chunk_id)
    outcome = services.questions.answer(question_id, answer=request.answer, answered_by=resolved_username(http_request))
    result = AnswerResult(
        won=outcome.won,
        question_id=outcome.question_id,
        answer=outcome.answer,
        answered_by=outcome.answered_by,
        answered_at=iso_utc(outcome.answered_at),
    )
    if not outcome.won:
        # A racing second answer — the loser is told who already answered (the same
        # first-write-wins pattern as a gate decision).
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=result.model_dump())
    # The winning answer row alone flips the chunk out of waiting_on_human.
    winner = services.chunks.get_question(question_id)
    if winner is not None:
        key = f"question_answers:{question_id}"
        services.events.publish_question_answered(winner.chunk_id, question_id, key=key)
        chunk_events.ChunkChanged.of(services, winner.chunk_id, prev_status=change.prev_status).publish(
            cause="question-answered", key=key
        )
    return result


@router.get("/questions", response_model=list[QuestionView], dependencies=[Depends(require(FLEET_VIEW))])
def list_open_questions(services: Annotated[HubServices, Depends(get_services)]) -> list[QuestionView]:
    """Every open (unanswered) question across the fleet — the ``hub status`` surface."""
    return [question_view(row) for row in services.chunks.list_open_questions()]
