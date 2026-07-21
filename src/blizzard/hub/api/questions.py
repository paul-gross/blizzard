"""Question routes — the anonymous **operator** half of the ask/answer rendezvous
(issue #87, #104).

``POST /questions`` lands the durable question row a runner forwards (the chunk
derives ``waiting_on_human``); ``POST /questions/{id}/answers`` writes the answer
**first-write-wins** (201 for the winner, 409 carrying the winning answer for a racing
loser) — the board's person answering it; and ``GET /questions`` lists the open ones for
``blizzard hub status``. Controllers stay read-only over the store and delegate the
writes to :class:`~blizzard.hub.domain.questions.QuestionService`
(``bzh:controller-read-only``).

The runner's own answer poll (``GET /questions/{id}``) moved to the
runner-authenticated fleet router (:mod:`blizzard.hub.api.fleet`, issue #87) — no board
or CLI caller ever reached it. :func:`question_view` stays here, public, so the fleet
router's own poll reuses this module's rendering rather than duplicating it.
``dependencies=[Depends(reject_runner_principal)]`` rejects a runner's bearer token on
this router rather than treating it as anonymous-plus-credential.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import FLEET_VIEW, QUESTION_ANSWER
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, resolved_username
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.work import ChunkFacts, QuestionRow, derive_chunk_status
from blizzard.wire.question import AnswerRequest, AnswerResult, QuestionAsked, QuestionView

router = APIRouter(prefix="/api", tags=["questions"], dependencies=[Depends(reject_runner_principal)])


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
        asked_at=iso_utc(row.asked_at),
        answered=row.answered,
        answer=row.answer,
        answered_by=row.answered_by,
        answered_at=iso_utc(row.answered_at) if row.answered_at is not None else None,
    )


@router.post("/questions", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require(QUESTION_ANSWER))])
def ask_question(fact: QuestionAsked, services: Annotated[HubServices, Depends(get_services)]) -> dict[str, str]:
    """Land a ``question.asked`` row — the chunk parks ``waiting_on_human``."""
    if services.chunks.get(fact.chunk_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {fact.chunk_id}")
    services.questions.record_asked(fact)
    services.events.publish_question_asked(fact.chunk_id, fact.question_id)
    _publish(services, fact.chunk_id)
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

    ``answered_by`` is taken from the resolved session identity
    (:func:`~blizzard.hub.api.auth_session.resolved_username`), never the request
    body's ``answered_by`` field — a spoofed value there is silently ignored (issue #91)."""
    if services.chunks.get_question(question_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown question {question_id}")
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
        services.events.publish_question_answered(winner.chunk_id, question_id)
        _publish(services, winner.chunk_id)
    return result


@router.get("/questions", response_model=list[QuestionView], dependencies=[Depends(require(FLEET_VIEW))])
def list_open_questions(services: Annotated[HubServices, Depends(get_services)]) -> list[QuestionView]:
    """Every open (unanswered) question across the fleet — the ``hub status`` surface."""
    return [question_view(row) for row in services.chunks.list_open_questions()]


def _publish(services: HubServices, chunk_id: str) -> None:
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    services.events.publish_chunk_changed(chunk_id, derive_chunk_status(facts).value)
