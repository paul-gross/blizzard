"""The ask/answer domain rule ([ask-answer.md] / domain/questions.md).

The one genuinely new primitive: a worker facing an undecidable choice asks and the
chunk parks — ``waiting_on_human`` — until the answer arrives and the dormant session
is resumed around it. This service owns the hub half: landing the durable question
row a runner forwards, and applying the **first-write-wins CAS** answer (the store's
answer-row primary key is the fence; the loser is told who won). Open/answered is
never stored — it derives from the answer row's presence (D-004, ``bzh:facts-not-status``).

The controllers stay read-only over the store (``bzh:controller-read-only``); the
service holds the write chunk repository and stamps landing times from the injected
clock (``bzh:injected-clock``).
"""

from __future__ import annotations

from datetime import datetime

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.work import AnswerOutcome, IWriteChunkRepository
from blizzard.wire.question import QuestionAsked

_log = get_logger("blizzard.hub.questions")


class QuestionService:
    """Land questions and answers at the hub ([ask-answer.md])."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def record_asked(self, fact: QuestionAsked) -> None:
        """Land a ``question.asked`` row — the chunk derives ``waiting_on_human`` (D-004)."""
        self._chunks.record_question(
            question_id=fact.question_id,
            chunk_id=fact.chunk_id,
            node_id=fact.node_id,
            session_id=fact.session_id,
            runner_id=fact.runner_id,
            epoch=fact.epoch,
            question=fact.question,
            options=fact.options,
            asked_at=_parse(fact.asked_at, self._clock),
        )
        _log.info("question landed", question_id=fact.question_id, chunk_id=fact.chunk_id)

    def answer(self, question_id: str, *, answer: str, answered_by: str) -> AnswerOutcome:
        """Apply the answer first-write-wins ([ask-answer.md]); the CAS lives in the store."""
        outcome = self._chunks.answer_question(
            question_id, answer=answer, answered_by=answered_by, at=self._clock.now()
        )
        _log.info("answer applied", question_id=question_id, won=outcome.won, answered_by=outcome.answered_by)
        return outcome

    def record_delivered(self, *, question_id: str, chunk_id: str) -> None:
        """Record an ``answer.delivered`` fact — the resume-with-answer ran (board detail)."""
        self._chunks.record_answer_delivered(question_id=question_id, chunk_id=chunk_id, at=self._clock.now())


def _parse(value: str, clock: IClock) -> datetime:
    """Read an ISO-8601 instant, falling back to now on a malformed stamp."""
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return clock.now()
