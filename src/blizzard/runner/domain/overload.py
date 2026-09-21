"""Provider-overload backoff facts and policy (blizzard#595).

An overloaded harness invocation (worker or judge) is not judged, consumes no retry, and
keeps its epoch — the same lease resumes in place after an exponential backoff. Facts are
durable and append-only (``bzh:facts-not-status``), mirroring
``blizzard.runner.domain.checks``'s own nudge-fact shape."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.domain.elicitation import IReadElicitationRepository

__all__ = [
    "BACKOFF_BASE_SECONDS",
    "BACKOFF_CAP_SECONDS",
    "BACKOFF_LIMIT",
    "IReadOverloadRepository",
    "IWriteOverloadRepository",
    "InvocationKind",
    "OverloadFactRecord",
    "backing_off_facts",
    "backoff_delay",
]

InvocationKind = Literal["worker", "judge"]

#: The declared policy (D6): the first overload in a streak backs off this long, doubling.
BACKOFF_BASE_SECONDS = 60
#: Part of the policy formula; never actually reached at ``BACKOFF_LIMIT``.
BACKOFF_CAP_SECONDS = 15 * 60
#: The streak ordinal that falls through to today's behavior instead of backing off again.
BACKOFF_LIMIT = 5


@dataclass(frozen=True)
class OverloadFactRecord:
    """One recorded overload exit on a (lease, epoch) — ``resume_after`` is ``None`` for a
    fall-through (the streak hit :data:`BACKOFF_LIMIT`) rather than a scheduled wake.
    ``invocation_identity`` is the generation (worker) or the elicitation's own launch
    instant (judge) — whichever this backoff closes against once that invocation moves on."""

    lease_id: str
    chunk_id: str
    epoch: int
    generation: int
    invocation_kind: InvocationKind
    invocation_identity: str
    streak_ordinal: int
    observed_at: datetime
    resume_after: datetime | None


def backoff_delay(streak_ordinal: int) -> timedelta:
    """The wait before this streak position's own resume (D6) — 60/120/240/480s for
    ordinals 1-4, capped at 15 minutes (never reached at :data:`BACKOFF_LIMIT`).
    ``streak_ordinal`` is 1-indexed: the first overload in a streak backs off 60s."""
    seconds = min(BACKOFF_BASE_SECONDS * (2 ** (streak_ordinal - 1)), BACKOFF_CAP_SECONDS)
    return timedelta(seconds=seconds)


class IReadOverloadRepository(Protocol):
    """Read-only overload-backoff queries (held by read-path edges)."""

    def overload_streak(self, lease_id: str, epoch: int) -> int:
        """Overload rows recorded since the latest reset on this (lease, epoch) — read
        fresh each classification, never cached (``bzh:facts-not-status``)."""
        ...

    def open_overload_facts(self) -> list[OverloadFactRecord]:
        """Every un-reset overload fact with a non-null ``resume_after`` — the backing-off
        candidates a caller narrows further against the lease's own current generation or
        elicitation launch (D7), read once per tick like ``pause_parked_lease_ids``."""
        ...


class IWriteOverloadRepository(IReadOverloadRepository, Protocol):
    """Read-write overload-backoff store — held only by the domain."""

    def record_overload(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        epoch: int,
        generation: int,
        invocation_kind: InvocationKind,
        invocation_identity: str,
        streak_ordinal: int,
        observed_at: datetime,
        resume_after: datetime | None,
    ) -> None:
        """Durably record one overload exit (D5). Insert-if-absent keyed on
        ``(lease_id, epoch, invocation_kind, invocation_identity)`` — re-classifying the
        same exit on a later pass writes nothing, mirroring ``nudge_facts``'s own
        check-then-insert (``bzh:sql-portable``)."""
        ...

    def record_reset(self, *, lease_id: str, epoch: int, at: datetime) -> None:
        """Durably close every open overload fact on this (lease, epoch) — written only
        when a clean exit finds a streak open there (D5), so a later overload starts a
        fresh streak at ordinal 1."""
        ...


class _IReadLeaseGeneration(Protocol):
    """The one lease-liveness read :func:`backing_off_facts` needs — declared locally
    rather than imported from ``domain.leases``, which itself imports this module for
    :class:`~blizzard.runner.domain.leases.LocalLeaseService`'s own use of
    :func:`backing_off_facts` (a cycle either direction's concrete import would close)."""

    def lease_generation(self, lease_id: str) -> int: ...


def backing_off_facts(
    overload: IReadOverloadRepository, liveness: _IReadLeaseGeneration, elicitations: IReadElicitationRepository
) -> dict[str, OverloadFactRecord]:
    """Every active lease currently backing off, keyed by lease id — read once per tick
    like ``pause_parked_lease_ids`` (D7). A candidate closes implicitly: a worker's own
    generation moving past the recorded one, or a judge's elicitation relaunching under a
    fresh identity, both mean this invocation was already acted on, with no separate
    closing write."""
    result: dict[str, OverloadFactRecord] = {}
    for fact in overload.open_overload_facts():
        if fact.invocation_kind == "worker":
            if str(liveness.lease_generation(fact.lease_id)) != fact.invocation_identity:
                continue
        else:
            elicitation = elicitations.in_flight_elicitation(fact.lease_id, fact.epoch)
            if elicitation is None or iso_utc(elicitation.first_launched_at) != fact.invocation_identity:
                continue
        result[fact.lease_id] = fact
    return result
