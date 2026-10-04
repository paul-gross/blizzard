"""An in-flight judgement elicitation — the detached process a launch starts and a
later reconciliation pass collects, keyed ``(lease_id, epoch)``.

The record's rules live here (``bzh:domain-orchestration-split``): its staleness bound, when it still
waits, its next attempt index, and its verb table. Each takes plain values, the current instant
included, so it is pinned by value."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.foundation.store.utc import as_utc

__all__ = [
    "ELICITATION_STALENESS_THRESHOLD",
    "ElicitationNotRecorded",
    "ElicitationState",
    "ElicitationTransition",
    "ElicitationVerb",
    "IReadElicitationRepository",
    "IWriteElicitationRepository",
    "PendingElicitation",
]

#: A lost elicitation's relaunch/abandon bound, measured from the FIRST launch for a
#: `(lease, epoch)` and never reset by a relaunch — short, since a model turn that has
#: not written a byte in this long is presumed crash-looping, not merely slow.
ELICITATION_STALENESS_THRESHOLD = timedelta(minutes=15)


class ElicitationState(StrEnum):
    """Whether a ``(lease, epoch)`` holds an in-flight elicitation record."""

    ABSENT = "absent"
    STANDING = "standing"

    @classmethod
    def of(cls, record: PendingElicitation | None) -> ElicitationState:
        return cls.ABSENT if record is None else cls.STANDING

    def on(self, verb: ElicitationVerb) -> ElicitationTransition:
        """What ``verb`` does to a record in this state — the one declared table every
        record write reads."""
        return _TRANSITIONS[verb][self]


class ElicitationVerb(StrEnum):
    """Every write against the in-flight record: a fresh ``launch``, the launched
    process's ``started`` fill-in, a lost answer's ``relaunch``, and the ``clear`` on collect
    or lease closure."""

    LAUNCH = "launch"
    STARTED = "started"
    RELAUNCH = "relaunch"
    CLEAR = "clear"


class ElicitationTransition(StrEnum):
    """A verb's effect from one state: it applies, it writes nothing, or it is refused."""

    APPLY = "apply"
    NOOP = "noop"
    REFUSE = "refuse"


# A launch over a standing record restarts the bound; ``started`` and ``relaunch`` refuse an absent record.
_TRANSITIONS: Mapping[ElicitationVerb, Mapping[ElicitationState, ElicitationTransition]] = MappingProxyType(
    {
        ElicitationVerb.LAUNCH: {
            ElicitationState.ABSENT: ElicitationTransition.APPLY,
            ElicitationState.STANDING: ElicitationTransition.APPLY,
        },
        ElicitationVerb.STARTED: {
            ElicitationState.ABSENT: ElicitationTransition.REFUSE,
            ElicitationState.STANDING: ElicitationTransition.APPLY,
        },
        ElicitationVerb.RELAUNCH: {
            ElicitationState.ABSENT: ElicitationTransition.REFUSE,
            ElicitationState.STANDING: ElicitationTransition.APPLY,
        },
        ElicitationVerb.CLEAR: {
            ElicitationState.ABSENT: ElicitationTransition.NOOP,
            ElicitationState.STANDING: ElicitationTransition.APPLY,
        },
    }
)


class ElicitationNotRecorded(Exception):
    """A record write the table refuses on an absent record: ``started`` or ``relaunch``
    named a ``(lease, epoch)`` with no in-flight elicitation."""

    def __init__(self, verb: ElicitationVerb, lease_id: str, epoch: int) -> None:
        super().__init__(f"no in-flight elicitation for lease {lease_id} epoch {epoch} to record {verb.value} on")
        self.verb = verb
        self.lease_id = lease_id
        self.epoch = epoch


@domain_model
@dataclass(frozen=True)
class PendingElicitation:
    """A launched elicitation not yet collected. ``pid``/``process_start_time`` are unset
    only in the un-armable gap between the durable record and the process actually
    starting (``advance.after-elicit-record.before-launch``)."""

    id: int  # the store's own row id, stable for the record's lifetime
    lease_id: str
    epoch: int
    pid: int | None
    process_start_time: str | None
    pgid: int | None  # this launch's owned process group; unset with `pid` alike
    output_path: str
    first_launched_at: datetime
    relaunch_count: int

    def stale(self, at: datetime) -> bool:
        """Past its bound at ``at`` — measured from the first launch, so a relaunch never
        buys the attempt more time."""
        return at - as_utc(self.first_launched_at) > ELICITATION_STALENESS_THRESHOLD

    def pending(self, at: datetime, *, alive: bool) -> bool:
        """Still waiting on its process at ``at``: live and under the bound. A live
        process past the bound is a hung one to fail; an exited one is to collect."""
        return alive and not self.stale(at)

    @property
    def next_attempt(self) -> int:
        """The attempt index a relaunch writes its fresh output file under."""
        return self.relaunch_count + 1


class IReadElicitationRepository(Protocol):
    """Read-only in-flight-elicitation queries (held by read-path edges)."""

    def in_flight_elicitation(self, lease_id: str, epoch: int) -> PendingElicitation | None:
        """This lease's in-flight elicitation for ``epoch``, or ``None`` once collected,
        cleared on lease closure, or never launched."""
        ...

    def in_flight_elicitations(self, pairs: Sequence[tuple[str, int]]) -> dict[tuple[str, int], PendingElicitation]:
        """:meth:`in_flight_elicitation` for every ``(lease_id, epoch)`` pair in ``pairs``,
        keyed by that same pair (`bzh:bulk-reconstitution`). A pair with no record is
        absent, exactly as the singular getter answers ``None`` for it."""
        ...

    def in_flight_elicitation_lease_ids(self) -> set[str]:
        """Every lease id with an in-flight elicitation record, regardless of epoch: no
        path may re-mint or resume a lease while its elicitation is in flight, matching the
        already-established ``parked_lease_ids``/``pending_submission_lease_ids`` shape."""
        ...

    def in_flight_elicitations_by_lease(self) -> dict[str, PendingElicitation]:
        """Every in-flight elicitation record, by lease id (regardless of epoch, matching
        :meth:`in_flight_elicitation_lease_ids`'s own shape). Hoisted once per tick
        (``bzh:bulk-reconstitution``) beside ``open_pause_parks`` for the pause park's
        teardown over later ticks, rather than one singular read per park per tick."""
        ...


class IWriteElicitationRepository(IReadElicitationRepository, Protocol):
    """Read-write in-flight-elicitation store — held only by the domain."""

    def record_elicitation_launch(self, lease_id: str, epoch: int, *, output_path: str, at: datetime) -> None:
        """Durably record a fresh launch BEFORE the process starts —
        ``pid``/``process_start_time`` land via :meth:`record_elicitation_started` once
        ``Popen`` returns."""
        ...

    def record_elicitation_started(
        self, lease_id: str, epoch: int, *, pid: int, process_start_time: str, pgid: int | None = None
    ) -> None:
        """Fill in the launched process's pid, start time, and owned group on
        ``Popen`` return — the same group-ownership fact a fresh spawn or resume records,
        so a lease closing mid-elicitation can group-kill it rather than a bare pid kill.
        Raises :class:`ElicitationNotRecorded` when no record stands for ``(lease_id, epoch)``."""
        ...

    def record_elicitation_relaunch(self, lease_id: str, epoch: int, *, output_path: str) -> None:
        """A lost answer's relaunch: a fresh ``output_path`` and pid slot, ``relaunch_count``
        incremented, ``first_launched_at`` left untouched — staleness is measured from the
        first launch and a relaunch never resets it. Raises :class:`ElicitationNotRecorded`
        when no record stands for ``(lease_id, epoch)``."""
        ...

    def clear_elicitation(self, lease_id: str, epoch: int) -> None:
        """Retire the record once its verdict is collected, or once its lease closes out
        from under it."""
        ...
