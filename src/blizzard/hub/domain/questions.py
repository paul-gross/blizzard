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
from blizzard.hub.domain.work import AnswerOutcome, IWriteChunkRepository
from blizzard.wire.question import QuestionAsked

_log = get_logger("blizzard.hub.questions")


class QuestionService:
    """Land questions and answers at the hub."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def record_asked(self, fact: QuestionAsked) -> None:
        """Land a ``question.asked`` row — the chunk derives ``waiting_on_human``."""
        self._chunks.record_question(
            question_id=fact.question_id,
            chunk_id=fact.chunk_id,
            node_id=fact.node_id,
            session_id=fact.session_id,
            runner_id=fact.runner_id,
            epoch=fact.epoch,
            question=fact.question,
            options=fact.options,
            asked_at=self._asked_at(fact.asked_at),
        )
        _log.info("question landed", question_id=fact.question_id, chunk_id=fact.chunk_id)

    def _asked_at(self, value: str) -> datetime:
        """Read an ISO-8601 instant, falling back to now on a malformed stamp.

        Coerces a naive result to UTC (``bzh:utc-instants``, issue #28); pinned by
        ``tests/test_ask_answer.py``."""
        try:
            return as_utc(datetime.fromisoformat(value))
        except ValueError:
            return self._clock.now()

    def answer(self, question_id: str, *, answer: str, answered_by: str) -> AnswerOutcome:
        """Apply the answer first-write-wins; the CAS lives in the store."""
        outcome = self._chunks.answer_question(
            question_id, answer=answer, answered_by=answered_by, at=self._clock.now()
        )
        _log.info("answer applied", question_id=question_id, won=outcome.won, answered_by=outcome.answered_by)
        return outcome

    def record_delivered(self, *, question_id: str, chunk_id: str) -> None:
        """Record an ``answer.delivered`` fact — the resume-with-answer ran (board detail)."""
        self._chunks.record_answer_delivered(question_id=question_id, chunk_id=chunk_id, at=self._clock.now())
