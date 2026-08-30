"""The worker ask/park repository seam (blizzard#410).

A dormant lease reads as parked either on an unanswered question or on an operator
pause (:mod:`~blizzard.runner.domain.pause`); :meth:`IReadAskRepository.parked_lease_ids`
is their union, read by every collaborator that only needs "is this lease dormant"."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

__all__ = ["AskRecord", "IReadAskRepository", "IWriteAskRepository", "ParkRecord"]


@dataclass(frozen=True)
class AskRecord:
    """The worker's local open-ask fact.

    ``question_id`` is runner-minted so the answer polls back by it; ``session_id`` is
    the dormant session the resume-with-answer targets."""

    lease_id: str
    chunk_id: str
    question_id: str
    question: str
    options: list[str]
    session_id: str | None
    asked_at: datetime


@dataclass(frozen=True)
class ParkRecord:
    """A lease's park on a question — dormant, no live worker."""

    lease_id: str
    chunk_id: str
    question_id: str
    parked_at: datetime


class IReadAskRepository(Protocol):
    """Read-only ask/park queries (held by read-path edges)."""

    def unforwarded_ask(self, lease_id: str) -> AskRecord | None:
        """The lease's newest ask not yet parked — its question_id has no park fact.

        Once parked, the park fact references the question_id, so the same ask is not
        re-parked; a resumed worker that asks *again* mints a fresh question_id,
        returned anew."""
        ...

    def parked_lease_ids(self) -> set[str]:
        """Leases dormant on a question **or an operator pause** — the union of
        :meth:`ask_parked_lease_ids` and :mod:`~blizzard.runner.domain.pause`'s own
        ``pause_parked_lease_ids`` (issue #46). A parked lease has no live worker, so
        REAP's stall clock does not apply ([ask-answer.md])."""
        ...

    def ask_parked_lease_ids(self) -> set[str]:
        """Leases dormant on a question — a park fact with no later resume ([ask-answer.md]).

        The ask-park half of :meth:`parked_lease_ids`'s union."""
        ...

    def open_park(self, lease_id: str) -> ParkRecord | None:
        """The lease's open park (park fact, no resume), or None — its question_id."""
        ...

    def open_asks(self) -> list[AskRecord]:
        """Every ask with no answer yet — forwarded-and-parked or still unforwarded (issue #51).

        An ask is open while its ``question_id`` carries no
        :meth:`~IWriteAskRepository.record_park_resume`, whether or not it has been
        forwarded up yet."""
        ...


class IWriteAskRepository(IReadAskRepository, Protocol):
    """Read-write ask/park store — held only by the domain."""

    def record_ask(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        question_id: str,
        question: str,
        options: list[str],
        session_id: str | None,
        asked_at: datetime,
    ) -> None:
        """Persist the worker's local open-ask fact."""
        ...

    def record_park(self, *, lease_id: str, chunk_id: str, question_id: str, parked_at: datetime) -> None:
        """Park a lease on a question — dormant, its env bindings held."""
        ...

    def record_park_resume(self, *, lease_id: str, question_id: str, resumed_at: datetime) -> None:
        """End a lease's park — the answer arrived and the session was resumed."""
        ...
