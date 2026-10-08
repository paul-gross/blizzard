"""Provider-overload backoff facts and policy.

An overloaded harness invocation (worker or judge) is not judged, consumes no retry, and
keeps its epoch — the same lease resumes in place after an exponential backoff. Facts are
durable and append-only (``bzh:facts-not-status``), mirroring
``blizzard.runner.lifecycle.judgement.checks``'s own nudge-fact shape."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from blizzard.foundation.roles import domain_model
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.leases.elicitation import IReadElicitationRepository

__all__ = [
    "BACKOFF_BASE_SECONDS",
    "BACKOFF_CAP_SECONDS",
    "BACKOFF_LIMIT",
    "IReadOverloadRepository",
    "IWriteOverloadRepository",
    "InvocationKind",
    "OverloadExit",
    "OverloadStreak",
    "backing_off_facts",
    "backoff_delay",
]

InvocationKind = Literal["worker", "judge"]

#: The declared policy: the first overload in a streak backs off this long, doubling.
BACKOFF_BASE_SECONDS = 60
#: Part of the policy formula; never actually reached at ``BACKOFF_LIMIT``.
BACKOFF_CAP_SECONDS = 15 * 60
#: The streak ordinal that falls through to today's behavior instead of backing off again.
BACKOFF_LIMIT = 5


@domain_model
@dataclass(frozen=True)
class OverloadExit:
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

    @property
    def backing_off(self) -> bool:
        """The lease waits out this fact and resumes in place; a fall-through does not."""
        return self._backing_off()

    def _backing_off(self) -> bool:
        return self.resume_after is not None

    def due(self, now: datetime) -> bool:
        """The backoff has run out at ``now``; a fall-through is never due."""
        return self.resume_after is not None and now >= self.resume_after

    def still_open(self, *, generation: int | None, elicitation_launched_at: datetime | None) -> bool:
        """This fact still holds its lease back: the invocation it closes against has not moved
        on. A worker fact holds only while the lease is still at its recorded generation; a judge
        fact only while the lease's in-flight elicitation keeps the launch it recorded — a
        relaunch, or no elicitation at all, means the invocation was already acted on."""
        if self.invocation_kind == "worker":
            return str(generation or 0) == self.invocation_identity
        return elicitation_launched_at is not None and iso_utc(elicitation_launched_at) == self.invocation_identity

    def settled(self, standing: OverloadExit | None) -> OverloadExit:
        """The fact that stands once this one is recorded: an exit already recorded for the same
        invocation keeps its own ordinal and decision, since re-classifying it writes nothing."""
        return standing if standing is not None else self


@domain_model
@dataclass(frozen=True)
class OverloadStreak:
    """The overload exits recorded on one (lease, epoch) since its latest reset."""

    count: int

    @property
    def open(self) -> bool:
        """A streak is open once one overload stands unreset — only then does a clean exit reset it."""
        return self._open()

    def _open(self) -> bool:
        return self.count > 0

    def next_fact(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        epoch: int,
        generation: int,
        invocation_kind: InvocationKind,
        invocation_identity: str,
        at: datetime,
    ) -> OverloadExit:
        """The next overload exit in this streak. Short of :data:`BACKOFF_LIMIT` it backs off for
        its ordinal's :func:`backoff_delay`; at the limit it falls through to the ordinary path."""
        ordinal = self.count + 1
        return OverloadExit(
            lease_id=lease_id,
            chunk_id=chunk_id,
            epoch=epoch,
            generation=generation,
            invocation_kind=invocation_kind,
            invocation_identity=invocation_identity,
            streak_ordinal=ordinal,
            observed_at=at,
            resume_after=at + backoff_delay(ordinal) if ordinal < BACKOFF_LIMIT else None,
        )


def backoff_delay(streak_ordinal: int) -> timedelta:
    """The wait before this streak position's own resume — 60/120/240/480s for
    ordinals 1-4, capped at 15 minutes (never reached at :data:`BACKOFF_LIMIT`).
    ``streak_ordinal`` is 1-indexed: the first overload in a streak backs off 60s."""
    seconds = min(BACKOFF_BASE_SECONDS * (2 ** (streak_ordinal - 1)), BACKOFF_CAP_SECONDS)
    return timedelta(seconds=seconds)


class IReadOverloadRepository(Protocol):
    """Read-only overload-backoff queries."""

    def overload_streak(self, lease_id: str, epoch: int) -> int:
        """Overload rows recorded since the latest reset on this (lease, epoch) — read
        fresh each classification, never cached (``bzh:facts-not-status``)."""
        ...

    def open_overload_facts(self) -> list[OverloadExit]:
        """Every un-reset overload fact with a non-null ``resume_after`` — the backing-off
        candidates a caller narrows further against the lease's own current generation or
        elicitation launch, read once per tick like ``pause_parked_lease_ids``. A fact
        whose lease has since closed is excluded here too (``bzh:open-facts-declare-closure``):
        a hub-terminal ending never writes a reset or a later overload of its own, so without
        this the fact would otherwise stand forever."""
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
    ) -> OverloadExit | None:
        """Durably record one overload exit. Insert-if-absent keyed on
        ``(lease_id, epoch, invocation_kind, invocation_identity)`` — re-classifying the
        same exit on a later pass writes nothing and returns the exit already standing
        for that invocation; ``None`` when this call inserted it."""
        ...

    def record_reset(self, *, lease_id: str, epoch: int, at: datetime) -> None:
        """Durably close every open overload fact on this (lease, epoch) — written only
        when a clean exit finds a streak open there, so a later overload starts a
        fresh streak at ordinal 1."""
        ...


class _IReadLeaseGeneration(Protocol):
    """The one lease-liveness read :func:`backing_off_facts` needs — declared locally
    rather than imported from ``leases``, which itself imports this module for
    :class:`~blizzard.runner.leases.activity.LocalLeaseService`'s own use of
    :func:`backing_off_facts` (a cycle either direction's concrete import would close)."""

    def lease_generations(self, lease_ids: Sequence[str]) -> dict[str, int]: ...


def backing_off_facts(
    overload: IReadOverloadRepository, liveness: _IReadLeaseGeneration, elicitations: IReadElicitationRepository
) -> dict[str, OverloadExit]:
    """Every active lease currently backing off, keyed by lease id — read once per tick
    like ``pause_parked_lease_ids``. A candidate closes implicitly
    (:meth:`OverloadExit.still_open`), with no separate closing write. The per-fact
    generation and elicitation reads are each collapsed into one bulk read up front, keyed
    by exactly the ids this open-facts set names (`bzh:bulk-reconstitution`)."""
    facts = overload.open_overload_facts()
    generations = liveness.lease_generations([fact.lease_id for fact in facts if fact.invocation_kind == "worker"])
    elicitations_by_pair = elicitations.in_flight_elicitations(
        [(fact.lease_id, fact.epoch) for fact in facts if fact.invocation_kind != "worker"]
    )
    result: dict[str, OverloadExit] = {}
    for fact in facts:
        elicitation = (
            elicitations_by_pair.get((fact.lease_id, fact.epoch)) if fact.invocation_kind != "worker" else None
        )
        if fact.still_open(
            generation=generations.get(fact.lease_id),
            elicitation_launched_at=elicitation.first_launched_at if elicitation is not None else None,
        ):
            result[fact.lease_id] = fact
    return result
