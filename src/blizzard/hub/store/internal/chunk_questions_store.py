"""SQLAlchemy adapter for the chunk questions seam (package-private, blizzard#411 Phase 3).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.questions import IWriteChunkQuestionsRepository
from blizzard.hub.domain.work import AnswerOutcome, QuestionRow
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_rows import QUESTIONS


class ChunkQuestionsStore:
    """The chunk's asked/answered/delivered question facts."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def get_question(self, question_id: str) -> QuestionRow | None:
        with self._store.read("get_question") as conn:
            row = conn.execute(QUESTIONS.select.where(s.questions.c.question_id == question_id)).one_or_none()
            return QUESTIONS.of(row) if row is not None else None

    def list_open_questions(self) -> list[QuestionRow]:
        with self._store.read("list_open_questions") as conn:
            rows = conn.execute(
                QUESTIONS.select.where(
                    s.questions.c.question_id.not_in(select(s.question_answers.c.question_id))
                ).order_by(s.questions.c.asked_at)
            ).all()
            return [QUESTIONS.of(row) for row in rows]

    def load_questions(self, chunk_id: str) -> list[QuestionRow]:
        with self._store.read("load_questions") as conn:
            rows = conn.execute(
                QUESTIONS.select.where(s.questions.c.chunk_id == chunk_id).order_by(s.questions.c.asked_at)
            ).all()
            return [QUESTIONS.of(row) for row in rows]

    def record_question(
        self,
        *,
        question_id: str,
        chunk_id: str,
        node_id: str | None,
        session_id: str | None,
        runner_id: str,
        epoch: int,
        question: str,
        options: list[str],
        asked_at: datetime,
    ) -> None:
        # Idempotent by question_id: a store-and-forward replay re-lands the same row.
        with self._store.write("record_question") as conn:
            exists = conn.execute(
                select(s.questions.c.question_id).where(s.questions.c.question_id == question_id)
            ).first()
            if exists is not None:
                return
            conn.execute(
                s.questions.insert().values(
                    question_id=question_id,
                    chunk_id=chunk_id,
                    node_id=node_id,
                    session_id=session_id,
                    runner_id=runner_id,
                    epoch=epoch,
                    question=question,
                    options=json.dumps(options),
                    asked_at=asked_at,
                )
            )

    def answer_question(self, question_id: str, *, answer: str, answered_by: str, at: datetime) -> AnswerOutcome:
        # First-write-wins CAS: the answer row's PK is the question id, so a racing
        # second insert raises IntegrityError and the loser reads back the winner.
        try:
            with self._store.write("answer_question", expect=(IntegrityError,)) as conn:
                conn.execute(
                    s.question_answers.insert().values(
                        question_id=question_id, answer=answer, answered_by=answered_by, answered_at=at
                    )
                )
            return AnswerOutcome(
                won=True, question_id=question_id, answer=answer, answered_by=answered_by, answered_at=at
            )
        except IntegrityError:
            with self._store.read("answer_question_conflict_lookup") as conn:
                winner = conn.execute(
                    select(s.question_answers).where(s.question_answers.c.question_id == question_id)
                ).one()
            return AnswerOutcome(
                won=False,
                question_id=question_id,
                answer=winner.answer,
                answered_by=winner.answered_by,
                answered_at=winner.answered_at,
            )

    def record_answer_delivered(self, *, question_id: str, chunk_id: str, at: datetime) -> None:
        with self._store.write("record_answer_delivered") as conn:
            conn.execute(
                s.answer_deliveries.insert().values(question_id=question_id, chunk_id=chunk_id, delivered_at=at)
            )


def _conforms_questions(x: ChunkQuestionsStore) -> IWriteChunkQuestionsRepository:
    return x
