"""The worker ask/park repository seam.

:meth:`IReadAskRepository.parked_lease_ids` unions the leases parked on an unanswered question or an
operator pause. :func:`check_askable` accepts an ask against the active lease whatever it is doing, and
a newer unforwarded ask supersedes the older (:func:`unshadowed`); an ask against an open takeover's
closed reference lease is refused (:class:`AskOnClosedLease`), as nothing would ever forward it."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import QUESTION_PREFIX, Id
from blizzard.foundation.roles import dto
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.leases.worker_lease import WorkerLease, WorkerVerb

__all__ = [
    "ASK_TRANSITIONS",
    "AskOnClosedLease",
    "AskService",
    "AskState",
    "IReadAskRepository",
    "IWriteAskRepository",
    "OpenAsk",
    "QuestionPark",
    "check_askable",
    "unshadowed",
]


@dto
@dataclass(frozen=True)
class OpenAsk:
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
    harness_id: str | None = None

    @property
    def session(self) -> SessionReference | None:  # ast-grep-ignore: bzh:property-delegates
        if self.session_id is None:
            return None
        if self.harness_id is None:
            raise ValueError(f"ask on lease {self.lease_id} has session_id {self.session_id!r} but no harness_id")
        return SessionReference(self.harness_id, self.session_id)


@dto
@dataclass(frozen=True)
class QuestionPark:
    """A lease's park on a question — dormant, no live worker."""

    lease_id: str
    chunk_id: str
    question_id: str
    parked_at: datetime


class AskState(StrEnum):
    """Where one ask stands. Derived from facts, never stored (``bzh:facts-not-status``):
    unforwarded until a park fact names its question, forwarded until a park resume does."""

    UNFORWARDED = "unforwarded"
    FORWARDED = "forwarded"
    ANSWERED = "answered"
    SUPERSEDED = "superseded"


#: The states one ask may move to from each; only an unforwarded ask is superseded (:func:`unshadowed`).
ASK_TRANSITIONS: Mapping[AskState, frozenset[AskState]] = MappingProxyType(
    {
        AskState.UNFORWARDED: frozenset({AskState.FORWARDED, AskState.SUPERSEDED}),
        AskState.FORWARDED: frozenset({AskState.ANSWERED}),
        AskState.ANSWERED: frozenset(),
        AskState.SUPERSEDED: frozenset(),
    }
)


class AskOnClosedLease(Exception):
    """The ask names the closed reference lease an open takeover holds — the API edge maps
    this to ``409``."""


def check_askable(worker: WorkerLease) -> None:
    """Pass when ``worker`` may record an ask, else raise :class:`AskOnClosedLease`."""
    if not worker.accepts(WorkerVerb.ASK):
        raise AskOnClosedLease(
            f"lease {worker.lease.lease_id} is closed — an ask against a takeover's reference lease is never forwarded"
        )


def unshadowed(asks_newest_first: Iterable[OpenAsk], *, forwarded: Iterable[str]) -> list[OpenAsk]:
    """``asks_newest_first`` without the unforwarded asks a newer unforwarded ask on the
    same lease supersedes — the same "newest" :meth:`IReadAskRepository.unforwarded_ask`
    forwards. ``forwarded`` names the question ids already forwarded and parked; those asks
    always stay. Order is preserved."""
    parked = set(forwarded)
    seen: set[str] = set()
    kept: list[OpenAsk] = []
    for ask in asks_newest_first:
        if ask.question_id not in parked:
            if ask.lease_id in seen:
                continue
            seen.add(ask.lease_id)
        kept.append(ask)
    return kept


class IReadAskRepository(Protocol):
    """Read-only ask/park queries (held by read-path edges)."""

    def unforwarded_ask(self, lease_id: str) -> OpenAsk | None:
        """The lease's newest ask not yet parked — its question_id has no park fact.

        Once parked, the park fact references the question_id, so the same ask is not
        re-parked; a resumed worker that asks *again* mints a fresh question_id,
        returned anew."""
        ...

    def parked_lease_ids(self) -> set[str]:
        """Leases dormant on a question **or an operator pause** — the union of
        :meth:`ask_parked_lease_ids` and :mod:`~blizzard.runner.throttle.pause`'s own
        ``pause_parked_lease_ids``. A parked lease has no live worker, so
        it is exempt from staleness reclamation ([ask-answer.md])."""
        ...

    def ask_parked_lease_ids(self) -> set[str]:
        """Leases dormant on a question — a park fact with no later resume ([ask-answer.md]).

        The ask-park half of :meth:`parked_lease_ids`'s union."""
        ...

    def open_park(self, lease_id: str) -> QuestionPark | None:
        """The lease's open park (park fact, no resume), or None — its question_id."""
        ...

    def open_asks(self) -> list[OpenAsk]:
        """Every ask with no answer yet — forwarded-and-parked or still unforwarded — newest first.

        An ask is open while its ``question_id`` carries no :meth:`~IWriteAskRepository.record_park_resume`,
        except an unforwarded ask a newer unforwarded ask on its lease supersedes (:func:`unshadowed`)."""
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
        asked_at: datetime,
        session: SessionReference | None = None,
    ) -> None:
        """Persist the worker's local open-ask fact."""
        ...

    def record_park(self, *, lease_id: str, chunk_id: str, question_id: str, parked_at: datetime) -> None:
        """Park a lease on a question — dormant, its env bindings held."""
        ...

    def record_park_resume(self, *, lease_id: str, question_id: str, resumed_at: datetime) -> None:
        """End a lease's park — the answer arrived and the session was resumed."""
        ...


class AskService:
    """Composition-root-wired: the ask store, the clock, and the optional event publisher."""

    def __init__(
        self, store: IWriteAskRepository, clock: IClock, *, events: IRunnerEventPublisher | None = None
    ) -> None:
        self._store = store
        self._clock = clock
        self._events = events

    def record_ask(self, worker: WorkerLease, *, question: str, options: list[str]) -> str:
        """Record a worker's ask against its lease, minting the question id, or raise
        :class:`AskOnClosedLease`. ``worker`` is already resolved by the caller
        (``bzh:domain-takes-objects``)."""
        check_askable(worker)
        lease = worker.lease
        question_id = Id.mint(QUESTION_PREFIX, self._clock).value
        self._store.record_ask(
            lease_id=lease.lease_id,
            chunk_id=lease.chunk_id,
            question_id=question_id,
            question=question,
            options=options,
            session=lease.session,
            asked_at=self._clock.now(),
        )
        if self._events is not None:
            self._events.publish_ask_changed(lease.lease_id, lease.chunk_id, question_id, cause="asked")
        return question_id
