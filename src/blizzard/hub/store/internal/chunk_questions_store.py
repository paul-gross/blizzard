"""SQLAlchemy adapter for the chunk questions seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunk.model import AnswerOutcome, NodeQuestion
from blizzard.hub.domain.chunk.ports.fence import Claimant, EpochAdmission, FenceRefusal
from blizzard.hub.domain.chunk.ports.questions import IWriteChunkQuestionsRepository
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.store import schema as s

# Legacy question writes share chunk fencing and question row operations.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.chunk_rows import QUESTIONS, fence, lock_chunk_row

# Legacy question writes share the chunk terminal-state predicate.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.chunk_terminal_predicates import chunk_is_terminal
from blizzard.hub.store.scoped import ScopedConnections


class ChunkQuestionsStore:
    """The chunk's asked/answered/delivered question facts."""

    def __init__(self, store: ScopedConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def get_question(self, question_id: str) -> NodeQuestion | None:
        with self._store.read("get_question") as conn:
            row = conn.execute(
                QUESTIONS.select(self._store).where(s.questions.c.question_id == question_id)
            ).one_or_none()
            return QUESTIONS.of(row) if row is not None else None

    def list_open_questions(self, *, project: ProjectId | None = None) -> list[NodeQuestion]:
        """The unanswered questions whose chunk has not ended — the SQL mirror of
        :meth:`NodeQuestion.require_answerable`, pinned by ``tests/test_open_questions_closure.py``.
        Question ids are runner-minted, so the answered set is read within the scope too: another
        tenant's answer under the same id never hides this tenant's question. ``project`` narrows to
        one project's questions."""
        open_ = s.questions.c.question_id.not_in(
            self._store.select(s.question_answers, s.question_answers.c.question_id)
        ) & ~chunk_is_terminal(self._store, s.questions.c.chunk_id)
        if project is not None:
            open_ &= s.questions.c.project_id == project
        with self._store.read("list_open_questions") as conn:
            rows = conn.execute(
                QUESTIONS.select(self._store).where(open_).order_by(s.questions.c.asked_at, s.questions.c.question_id)
            ).all()
            return [QUESTIONS.of(row) for row in rows]

    def load_questions(self, chunk_id: str) -> list[NodeQuestion]:
        with self._store.read("load_questions") as conn:
            rows = conn.execute(
                QUESTIONS.select(self._store).where(s.questions.c.chunk_id == chunk_id).order_by(s.questions.c.asked_at)
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
        admission: EpochAdmission,
        claimant: Claimant | None = None,
        question: str,
        options: list[str],
        asked_at: datetime,
        harness_id: str | None = None,
    ) -> FenceRefusal | None:
        with self._store.write("record_question") as conn:
            lock_chunk_row(conn, chunk_id)
            exists = conn.execute(
                self._store.select(s.questions, s.questions.c.question_id).where(
                    s.questions.c.question_id == question_id
                )
            ).first()
            if exists is not None:
                return None
            refusal = fence(conn, chunk_id, epoch=epoch, admission=admission, claimant=claimant)
            if refusal is not None:
                return refusal
            project_id = conn.execute(
                self._store.select(s.chunks, s.chunks.c.project_id).where(s.chunks.c.chunk_id == chunk_id)
            ).scalar_one()
            conn.execute(
                self._store.insert(s.questions).values(
                    question_id=question_id,
                    chunk_id=chunk_id,
                    project_id=project_id,
                    node_id=node_id,
                    session_id=session_id,
                    harness_id=harness_id,
                    runner_id=runner_id,
                    epoch=epoch,
                    question=question,
                    options=json.dumps(options),
                    asked_at=asked_at,
                )
            )
            return None

    def answer_question(self, question_id: str, *, answer: str, answered_by: str, at: datetime) -> AnswerOutcome:
        # First-write-wins CAS: the answer row's PK is (tenant_id, question_id), so a racing
        # second insert in the same tenant raises IntegrityError and the loser reads back the winner.
        try:
            with self._store.write("answer_question", expect=(IntegrityError,)) as conn:
                conn.execute(
                    self._store.insert(s.question_answers).values(
                        question_id=question_id, answer=answer, answered_by=answered_by, answered_at=at
                    )
                )
            return AnswerOutcome(
                won=True, question_id=question_id, answer=answer, answered_by=answered_by, answered_at=at
            )
        except IntegrityError:
            with self._store.read("answer_question_conflict_lookup") as conn:
                winner = conn.execute(
                    self._store.select(s.question_answers).where(s.question_answers.c.question_id == question_id)
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
                self._store.insert(s.answer_deliveries).values(
                    question_id=question_id, chunk_id=chunk_id, delivered_at=at
                )
            )


_questions_conforms: type[IWriteChunkQuestionsRepository] = ChunkQuestionsStore
