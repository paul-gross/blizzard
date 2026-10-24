"""Question routes — the anonymous **operator** half of the ask/answer rendezvous (#87).

``POST /questions`` lands the durable question row (the chunk derives
``waiting_on_human``); ``POST /questions/{id}/answers`` writes **first-write-wins**
(201 for the winner, 409 carrying the winning answer for a racing loser).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import Permission
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import chunk_events
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.deps import Services
from blizzard.hub.api.project_label_fields import ProjectLabelFields
from blizzard.hub.api.project_labels import ProjectLabels
from blizzard.hub.api.project_params import QueryProject
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.domain.chunk.model import ChunkFacts, NodeQuestion, QuestionClosed, QuestionState
from blizzard.hub.domain.execution.questions import AskedQuestion
from blizzard.wire.question import AnswerRequest, AnswerResult, QuestionAsked, QuestionView

router = APIRouter(
    prefix="/api", tags=["questions"], dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)]
)


def question_view(
    row: NodeQuestion, runner_names: Mapping[str, str], project: ProjectLabelFields, *, superseded_by_restart: bool
) -> QuestionView:
    """Render a stored question row as its wire view — derived answer + delivery state, the
    asking runner's registered name out of ``runner_names``, the caller's one batched read, and
    the chunk's project labels. A restart is a chunk fact the row does not carry, so the caller
    says whether one answered this question for the system."""
    return QuestionView(
        question_id=row.question_id,
        chunk_id=row.chunk_id,
        node_id=row.node_id,
        session_id=row.session_id,
        harness_id=row.harness_id,
        runner_id=row.runner_id,
        runner_name=runner_names.get(row.runner_id),
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
        # An answer the runner delivered before the restart was not lost to it, so it is never "superseded".
        superseded=not row.delivered
        and row.state(superseded_by_restart=superseded_by_restart) is QuestionState.SUPERSEDED,
        **project,
    )


def asked_question(fact: QuestionAsked) -> AskedQuestion:
    return AskedQuestion(
        question_id=fact.question_id,
        chunk_id=fact.chunk_id,
        runner_id=fact.runner_id,
        epoch=fact.epoch,
        question=fact.question,
        asked_at=fact.asked_at,
        node_id=fact.node_id,
        session_id=fact.session_id,
        harness_id=fact.harness_id,
        lease_id=fact.lease_id,
        options=tuple(fact.options),
    )


@router.post(
    "/questions", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require(Permission.QUESTION_ANSWER))]
)
def ask_question(fact: QuestionAsked, services: Services) -> dict[str, str]:
    """Land a ``question.asked`` row — the chunk parks ``waiting_on_human``; 409 when fenced out."""
    chunk = services.chunks.record.get(fact.chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {fact.chunk_id}")
    change = chunk_events.ChunkChanged.before(services, fact.chunk_id)
    refusal = services.questions.record_asked(asked_question(fact))
    if refusal is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refusal.detail)
    key = f"questions:{fact.question_id}"
    services.events.publish_question_asked(fact.chunk_id, fact.question_id, key=key, project_id=chunk.project_id)
    change.publish(cause="question-asked", key=key)
    return {"question_id": fact.question_id}


@router.post("/questions/{question_id}/answers", response_model=AnswerResult, status_code=status.HTTP_201_CREATED)
def answer_question(
    question_id: str,
    request: AnswerRequest,
    services: Services,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.QUESTION_ANSWER))],
) -> object:
    """Answer a question first-write-wins; 409 carries the winning answer.

    ``answered_by`` is taken from the authenticated session identity, never the request
    body's ``answered_by`` field — a spoofed value there is silently ignored."""
    pre_answer = services.chunks.questions.get_question(question_id)
    if pre_answer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown question {question_id}")
    change = chunk_events.ChunkChanged.before(services, pre_answer.chunk_id)
    try:
        outcome = services.questions.answer(
            pre_answer, ChunkFacts.or_default(change.facts), answer=request.answer, answered_by=identity.username
        )
    except QuestionClosed as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
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
    # The winning answer row alone flips the chunk out of waiting_on_human; the question's
    # own chunk_id never changes, so the pre-answer read already named it — no re-read.
    key = f"question_answers:{question_id}"
    state = chunk_events.ChunkFrameState.load(services, pre_answer.chunk_id)
    services.events.publish_question_answered(
        pre_answer.chunk_id,
        question_id,
        key=key,
        project_id=state.chunk.project_id if state.chunk is not None else None,
    )
    chunk_events.ChunkChanged.of(services, pre_answer.chunk_id, prev_status=change.prev_status).publish_from(
        state, cause="question-answered", key=key
    )
    return result


@router.get("/questions", response_model=list[QuestionView], dependencies=[Depends(require(Permission.FLEET_VIEW))])
def list_open_questions(services: Services, project: QueryProject) -> list[QuestionView]:
    """Every open (unanswered) question across the fleet — the ``hub status`` surface — or only
    ``?project=``'s."""
    rows = services.chunks.questions.list_open_questions(project=project)
    runner_names = services.registry.names_for(row.runner_id for row in rows)
    labels = ProjectLabels.read(services, (row.project_id for row in rows))
    # A restart answers every open question in its one write, so an unanswered row is never superseded.
    return [
        question_view(row, runner_names, labels.fields(row.project_id), superseded_by_restart=False) for row in rows
    ]
