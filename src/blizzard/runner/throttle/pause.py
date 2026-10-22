"""The hub-mirrored/local pause brake and its repository seam.

The brake's rules live on :class:`LocalBrake` (which verbs are legal from which state is
:data:`BRAKE_TRANSITIONS`) and :class:`RunnerBrakes` (what the two brakes stop between them);
:class:`PauseService` only reads the clock, asks the model, and writes the fact it returns."""

from __future__ import annotations

import json
from collections.abc import Container, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.fact_kinds import RUNNER_LOCALLY_PAUSED, RUNNER_LOCALLY_RESUMED
from blizzard.foundation.roles import domain_model
from blizzard.foundation.spend import SpendFigure
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.events.publisher import IRunnerEventPublisher

__all__ = [
    "BRAKE_TRANSITIONS",
    "BrakeState",
    "BrakeVerb",
    "BrakeVerdict",
    "ILockedBrakes",
    "IReadPauseRepository",
    "IWritePauseRepository",
    "LocalBrake",
    "LocalPauseFact",
    "PausePark",
    "PauseService",
    "RunnerBrakes",
    "needs_pause_park",
    "spend_ceiling_reason",
]


class BrakeState(StrEnum):
    """Where this runner's own brake stands: released, engaged by a plain operator pause, or
    engaged with a durable reason (the spend ceiling)."""

    RELEASED = "released"
    ENGAGED = "engaged"
    ENGAGED_REASONED = "engaged_reasoned"


class BrakeVerb(StrEnum):
    """What can act on the local brake: the operator's pause and start, and the runner's own
    engagement (the spend ceiling crossing). There is no self-lift verb:
    only the operator's start clears the brake."""

    OPERATOR_PAUSE = "operator_pause"
    OPERATOR_START = "operator_start"
    SELF_ENGAGE = "self_engage"


class BrakeVerdict(StrEnum):
    """What a verb does from a state: writes a fact, or writes nothing."""

    LEGAL = "legal"
    NO_OP = "no_op"


#: Which brake verbs write a fact from which state; a pause over an engaged brake keeps its standing reason.
BRAKE_TRANSITIONS: Mapping[BrakeState, Mapping[BrakeVerb, BrakeVerdict]] = MappingProxyType(
    {
        BrakeState.RELEASED: MappingProxyType(
            {
                BrakeVerb.OPERATOR_PAUSE: BrakeVerdict.LEGAL,
                BrakeVerb.OPERATOR_START: BrakeVerdict.NO_OP,
                BrakeVerb.SELF_ENGAGE: BrakeVerdict.LEGAL,
            }
        ),
        BrakeState.ENGAGED: MappingProxyType(
            {
                BrakeVerb.OPERATOR_PAUSE: BrakeVerdict.NO_OP,
                BrakeVerb.OPERATOR_START: BrakeVerdict.LEGAL,
                BrakeVerb.SELF_ENGAGE: BrakeVerdict.NO_OP,
            }
        ),
        BrakeState.ENGAGED_REASONED: MappingProxyType(
            {
                BrakeVerb.OPERATOR_PAUSE: BrakeVerdict.NO_OP,
                BrakeVerb.OPERATOR_START: BrakeVerdict.LEGAL,
                BrakeVerb.SELF_ENGAGE: BrakeVerdict.NO_OP,
            }
        ),
    }
)


@domain_model
@dataclass(frozen=True)
class LocalPauseFact:
    """One local pause or start fact to append, with the hub-bound report it carries."""

    paused: bool
    by: str
    at: datetime
    #: The engagement's durable reason; ``None`` on an operator's pause or start.
    reason: str | None = None

    @property
    def report_kind(self) -> str:
        return self._report_kind()

    def _report_kind(self) -> str:
        return RUNNER_LOCALLY_PAUSED if self.paused else RUNNER_LOCALLY_RESUMED


@domain_model
@dataclass(frozen=True)
class LocalBrake:
    """This runner's own brake, reconstituted from its newest local pause fact."""

    TRANSITIONS: ClassVar[Mapping[BrakeState, Mapping[BrakeVerb, BrakeVerdict]]] = BRAKE_TRANSITIONS

    paused: bool
    #: The newest fact's reason; read only while engaged.
    reason: str | None = None

    @property
    def state(self) -> BrakeState:
        return self._state()

    def _state(self) -> BrakeState:
        if not self.paused:
            return BrakeState.RELEASED
        return BrakeState.ENGAGED_REASONED if self.reason is not None else BrakeState.ENGAGED

    def engage(self, *, by: str, reason: str, at: datetime) -> LocalPauseFact | None:
        """The runner's own engagement — once: an engaged brake, for any cause, keeps its
        standing fact and reason, and nothing is written."""
        if self.TRANSITIONS[self.state][BrakeVerb.SELF_ENGAGE] is BrakeVerdict.NO_OP:
            return None
        return LocalPauseFact(paused=True, by=by, at=at, reason=reason)

    def set_by_operator(self, *, paused: bool, by: str, at: datetime) -> LocalPauseFact | None:
        """The operator's pause or start. A pause over an engaged brake keeps the standing fact
        and its reason; a start over a released brake writes nothing. The start is the only
        verb that clears the brake."""
        verb = BrakeVerb.OPERATOR_PAUSE if paused else BrakeVerb.OPERATOR_START
        if self.TRANSITIONS[self.state][verb] is BrakeVerdict.NO_OP:
            return None
        return LocalPauseFact(paused=paused, by=by, at=at)


@domain_model
@dataclass(frozen=True)
class RunnerBrakes:
    """The runner's two brakes together: its own (``local``) and the hub's mirrored one (``hub``)."""

    local: bool
    hub: bool

    @property
    def blocks_claims(self) -> bool:
        """Either brake stops new claims."""
        return self._blocks_claims()

    def _blocks_claims(self) -> bool:
        return self.local or self.hub

    @property
    def starts_processes(self) -> bool:
        """Only the local brake stops every process start; the hub's brake stops claims alone."""
        return self._starts_processes()

    def _starts_processes(self) -> bool:
        return not self.local

    @property
    def effective(self) -> bool:
        """The runner reads as paused when either brake is engaged."""
        return self._effective()

    def _effective(self) -> bool:
        return self.local or self.hub


def needs_pause_park(lease_id: str, pause_parked: Container[str]) -> bool:
    """A lease on a paused chunk parks once: an open pause park absorbs every later pause."""
    return lease_id not in pause_parked


def spend_ceiling_reason(*, cap: float, window_hours: float, spend: SpendFigure) -> str:
    """The spend-ceiling engagement's durable reason, its spend rendered as the one figure."""
    return f"spend ceiling ${cap:.2f} reached over the trailing {window_hours:g}h (spend {spend.rendered()})"


@domain_model
@dataclass(frozen=True)
class PausePark:
    """One open pause park: a lease dormant on an operator pause,
    its interrupt's own facts."""

    lease_id: str
    chunk_id: str
    parked_at: datetime
    #: The elicitation record this park's interrupt signalled; unnamed-and-standing means a usage-limit judge park.
    interrupted_elicitation_id: int | None


class IReadPauseRepository(Protocol):
    """Read-only pause-brake queries."""

    def hub_contact_at(self) -> datetime | None:
        """The instant of the last successful hub contact, or ``None`` if never.

        The hub pause mirror is written only on a successful hub round trip, so its newest write
        **is** that instant — no separate fact needed (``bzh:facts-not-status``)."""
        ...

    def hub_paused(self) -> bool:
        """The last hub pause brake value mirrored locally; False when it has never been synced."""
        ...

    def local_paused(self) -> bool:
        """This runner's own brake, derived from the newest local pause fact.

        Distinct from ``hub_paused``: it blocks every spawn site, not claims alone.
        Defaults False when the operator has never set it."""
        ...

    def local_pause_reason(self) -> str | None:
        """The newest local pause fact's own reason — ``None`` on a plain
        operator pause, or when the brake has never been set. Read independently of
        :meth:`local_paused` so a caller decides for itself whether to consult it."""
        ...

    def pause_parked_lease_ids(self) -> set[str]:
        """Leases dormant on an operator pause — a pause-park fact with no later
        pause-resume at or after it and no closure of its lease.

        A hub-terminal chunk closes its lease, and the lease closure closes its park."""
        ...

    def open_pause_parks(self) -> dict[str, PausePark]:
        """Every open pause park by lease id — :meth:`pause_parked_lease_ids`'s leases, each with
        its ``parked_at`` and the elicitation its interrupt signalled, read in one query
        (``bzh:bulk-reconstitution``). A lease re-parked across a crash reads its newest park. A park closes with its
        lease: a hub-terminal chunk closes the lease, and the closed lease's park drops out of
        this read from then on."""
        ...


class ILockedBrakes(Protocol):
    """The handle of one open locked brake transaction — the domain-facing view, which declares no connection access.
    The store's own ``*_locked`` methods recover the transaction they run on from the handle."""


class IWritePauseRepository(IReadPauseRepository, Protocol):
    """Read-write pause-brake store — held only by the domain."""

    def set_hub_paused(self, *, paused: bool, at: datetime) -> None:
        """Mirror the hub's pause brake locally (upsert), stamped ``at``."""
        ...

    def record_local_pause(
        self,
        *,
        paused: bool,
        at: datetime,
        by: str,
        report_kind: str,
        report_payload: str,
        reason: str | None = None,
    ) -> int:
        """Append a local pause/start fact **and** its hub-bound report, atomically,
        and return the buffered report's seq. Appends rather than upserts:
        a locally-minted fact, not a mirror; taking the buffer entry here makes the
        brake and its report crash-atomic (``tests/test_ingest_and_pause_verbs.py``).
        ``reason`` is stored alongside the fact so :meth:`~IReadPauseRepository.local_pause_reason`
        can read it back locally, not only through the hub-bound report."""
        ...

    def record_pause_park(
        self, *, lease_id: str, chunk_id: str, parked_at: datetime, interrupted_elicitation_id: int | None = None
    ) -> None:
        """Park a lease on an operator pause — dormant, its env bindings held.
        ``interrupted_elicitation_id`` names the in-flight elicitation the park's own
        interrupt signalled, in the same insert (``bzh:facts-not-status``); ``None`` when it
        signalled none."""
        ...

    def record_pause_park_resume(self, *, lease_id: str, resumed_at: datetime) -> None:
        """End a lease's pause-park — the operator resumed it."""
        ...

    def locked_brakes(self) -> AbstractContextManager[ILockedBrakes]:
        """Open one write transaction over this runner's brake, locking its identity row first
        (``bzh:store-exclusive-write``), then yield the handle bound to that transaction. The brake is read and written
        through the ``*_locked`` methods, taking this same handle."""
        ...

    def local_brake_locked(self, handle: ILockedBrakes) -> LocalBrake:
        """This runner's brake, derived from its newest local pause fact on the handle's transaction."""
        ...

    def record_local_pause_locked(
        self,
        handle: ILockedBrakes,
        *,
        paused: bool,
        at: datetime,
        by: str,
        report_kind: str,
        report_payload: str,
        reason: str | None = None,
    ) -> int:
        """:meth:`record_local_pause` on the handle's transaction: the fact and its report commit or roll back with the
        brake read that decided them."""
        ...


def _report_payload(fact: LocalPauseFact) -> str:
    # The hub attributes the report to the runner whose token pushes it, so it names no runner.
    payload: dict[str, object] = {"by": fact.by, "at": iso_utc(fact.at)}
    if fact.reason is not None:
        payload["reason"] = fact.reason
    return json.dumps(payload)


class PauseService:
    """Composition-root-wired: the pause store, the clock, and the optional event
    publisher."""

    def __init__(
        self, store: IWritePauseRepository, clock: IClock, *, events: IRunnerEventPublisher | None = None
    ) -> None:
        self._store = store
        self._clock = clock
        self._events = events

    def set_local_pause(self, *, paused: bool, by: str) -> None:
        """Set this runner's own pause brake and its upward report, atomically — or write
        nothing when :meth:`LocalBrake.set_by_operator` finds it already in the requested state.

        The brake and its hub-bound report are one write: mirroring runs hub→runner only, so a brake
        never reported up would never be repaired (``tests/test_ingest_and_pause_verbs.py``). The write takes this
        runner's lock before it reads the brake, so the ceiling's engagement cannot race it."""
        with self._store.locked_brakes() as locked:
            fact = self._store.local_brake_locked(locked).set_by_operator(paused=paused, by=by, at=self._clock.now())
            seq = self._write_locked(locked, fact) if fact is not None else None
        if fact is not None and seq is not None:
            self._publish(seq, fact)

    def engage(self, *, by: str, reason: str) -> None:
        """Engage the local brake with a durable reason — once (:meth:`LocalBrake.engage`).
        The operator's clear (:meth:`set_local_pause` with ``paused=False``) is the only lift."""
        with self._store.locked_brakes() as locked:
            fact = self._store.local_brake_locked(locked).engage(by=by, reason=reason, at=self._clock.now())
            seq = self._write_locked(locked, fact) if fact is not None else None
        if fact is not None and seq is not None:
            self._publish(seq, fact)

    def _write_locked(self, locked: ILockedBrakes, fact: LocalPauseFact) -> int:
        return self._store.record_local_pause_locked(
            locked,
            paused=fact.paused,
            at=fact.at,
            by=fact.by,
            report_kind=fact.report_kind,
            report_payload=_report_payload(fact),
            reason=fact.reason,
        )

    def _publish(self, seq: int, fact: LocalPauseFact) -> None:
        if self._events is not None:
            self._events.publish_fact_changed(seq=seq, kind=fact.report_kind, chunk_id=None, lease_id=None)
