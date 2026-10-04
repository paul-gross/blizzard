"""The ask/answer domain rule.

Landing the durable question row (the chunk parks at ``waiting_on_human``) and applying
the **first-write-wins CAS** answer — the answer-row primary key is the fence, and the
loser is told who won. Open/answered derives from the answer row (``bzh:facts-not-status``).
"""

from __future__ import annotations

from datetime import datetime

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import as_utc
from blizzard.hub.domain.chunk.model import AnswerOutcome, ChunkFacts, NodeQuestion
from blizzard.hub.domain.chunk.ports.fence import Claimant, EpochAdmission, FenceRefusal
from blizzard.hub.domain.chunk.ports.questions import IWriteChunkQuestionsRepository
from blizzard.wire.question import QuestionAsked

_log = get_logger("blizzard.hub.questions")


def parse_instant(value: object, fallback: datetime) -> datetime:
    """An ISO-8601 instant coerced to UTC (``bzh:utc-instants``), or ``fallback`` for a missing or
    malformed stamp — a store-and-forward resend carries the naive stamp it buffered."""
    if not isinstance(value, str):
        return fallback
    try:
        return as_utc(datetime.fromisoformat(value))
    except ValueError:
        return fallback


class QuestionService:
    """Land questions and answers at the hub."""

    def __init__(self, *, questions: IWriteChunkQuestionsRepository, clock: IClock) -> None:
        self._questions = questions
        self._clock = clock

    def record_asked(self, fact: QuestionAsked) -> FenceRefusal | None:
        """Land a ``question.asked`` row — the chunk derives ``waiting_on_human`` — unless the
        write fence refuses it (``bzh:epoch-fencing``), which lands nothing."""
        refusal = self._questions.record_question(
            question_id=fact.question_id,
            chunk_id=fact.chunk_id,
            node_id=fact.node_id,
            session_id=fact.session_id,
            harness_id=fact.harness_id,
            runner_id=fact.runner_id,
            epoch=fact.epoch,
            admission=EpochAdmission.AT_OR_ABOVE,
            claimant=Claimant(fact.runner_id, fact.lease_id),
            question=fact.question,
            options=fact.options,
            asked_at=parse_instant(fact.asked_at, self._clock.now()),
        )
        if refusal is None:
            _log.info("question landed", question_id=fact.question_id, chunk_id=fact.chunk_id)
        return refusal

    def answer(self, question: NodeQuestion, facts: ChunkFacts, *, answer: str, answered_by: str) -> AnswerOutcome:
        """Apply the answer first-write-wins; the CAS lives in the store. Takes the loaded question
        and its chunk's facts (``bzh:domain-takes-objects``): an ended chunk's question raises
        :class:`~blizzard.hub.domain.chunk.model.QuestionClosed`."""
        question.require_answerable(facts.status())
        outcome = self._questions.answer_question(
            question.question_id, answer=answer, answered_by=answered_by, at=self._clock.now()
        )
        _log.info("answer applied", question_id=question.question_id, won=outcome.won, answered_by=outcome.answered_by)
        return outcome

    def record_delivered(self, *, question_id: str, chunk_id: str) -> None:
        """Record an ``answer.delivered`` fact — the resume-with-answer ran (board detail)."""
        self._questions.record_answer_delivered(question_id=question_id, chunk_id=chunk_id, at=self._clock.now())
