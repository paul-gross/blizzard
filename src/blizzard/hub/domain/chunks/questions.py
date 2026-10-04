"""The chunk-questions repository seam — a runner-authored question and its
first-write-wins answer."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.chunks.fence import Claimant, EpochAdmission, FenceRefusal
from blizzard.hub.domain.work import AnswerOutcome, NodeQuestion


class IReadChunkQuestionsRepository(Protocol):
    """Read-only chunk-questions access."""

    def get_question(self, question_id: str) -> NodeQuestion | None:
        """One question row with its derived answer state, or None."""
        ...

    def list_open_questions(self) -> list[NodeQuestion]:
        """Every unanswered question across the fleet — the ``hub status`` surface."""
        ...

    def load_questions(self, chunk_id: str) -> list[NodeQuestion]:
        """A chunk's questions, open and answered — the chunk-detail surface."""
        ...


class IWriteChunkQuestionsRepository(IReadChunkQuestionsRepository, Protocol):
    """Read-write chunk-questions access."""

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
        """Land a ``question.asked`` row — the chunk derives ``waiting_on_human`` — behind the
        write fence (``bzh:epoch-fencing``); a refusal writes nothing and is returned.

        Runner-authored, the durable rendezvous the answer keys off. Idempotent by
        ``question_id`` (a store-and-forward replay re-lands the same id harmlessly)."""
        ...

    def answer_question(self, question_id: str, *, answer: str, answered_by: str, at: datetime) -> AnswerOutcome:
        """First-write-wins CAS on the answer row.

        Exactly one answer row ever exists: the first write wins (``won=True``); a
        racing second write loses (``won=False``) and is handed the winning row. This
        row alone flips the chunk out of ``waiting_on_human``."""
        ...

    def record_answer_delivered(self, *, question_id: str, chunk_id: str, at: datetime) -> None:
        """Record an ``answer.delivered`` fact — the resume-with-answer ran.

        Detail only: the status already flipped at ``question.answered``, so no status
        derives from this."""
        ...
