"""The hub-mirrored/local pause brake and daemon-liveness repository seam.

The brake's rules live on :class:`LocalBrake` (which verbs are legal from which state is
:data:`BRAKE_TRANSITIONS`) and :class:`RunnerBrakes` (what the two brakes stop between them);
:class:`PauseService` only reads the clock, asks the model, and writes the fact it returns."""

from __future__ import annotations

import json
from collections.abc import Container, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.foundation.store.utc import as_utc, iso_utc
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.wire.facts import RUNNER_LOCALLY_PAUSED, RUNNER_LOCALLY_RESUMED

__all__ = [
    "BRAKE_TRANSITIONS",
    "BrakeState",
    "BrakeVerb",
    "BrakeVerdict",
    "IReadPauseRepository",
    "IWritePauseRepository",
    "LocalBrake",
    "LocalPauseFact",
    "PausePark",
    "PauseService",
    "RunnerBrakes",
    "needs_pause_park",
    "spend_ceiling_reason",
    "usage_limit_reason",
]


class BrakeState(StrEnum):
    """Where this runner's own brake stands: released, engaged by a plain operator pause, or
    engaged with a durable reason (the spend ceiling, a usage limit)."""

    RELEASED = "released"
    ENGAGED = "engaged"
    ENGAGED_REASONED = "engaged_reasoned"


class BrakeVerb(StrEnum):
    """What can act on the local brake: the operator's pause and start, and the runner's own
    engagement (the spend ceiling crossing, a usage-limited exit). There is no self-lift verb:
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


def spend_ceiling_reason(*, cap: float, window_hours: float, spend: float, partial: bool) -> str:
    """The spend-ceiling engagement's durable reason, flagging a lower-bound spend."""
    partial_note = " (PARTIAL — true spend may be higher)" if partial else ""
    return f"spend ceiling ${cap:.2f} reached over the trailing {window_hours:g}h (spend ${spend:.2f}{partial_note})"


def usage_limit_reason(harness_id: str, resets_at: datetime | None) -> str:
    """The usage-limit engagement's durable reason, naming the reset to the minute when known."""
    suffix = f" (resets {as_utc(resets_at).strftime('%Y-%m-%dT%H:%MZ')})" if resets_at is not None else ""
    return f"usage limit: {harness_id}{suffix}"


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
    """Read-only pause-brake and daemon-liveness queries (held by read-path edges)."""

    def hub_contact_at(self, runner_id: str) -> datetime | None:
        """When the runner last **successfully** reached the hub, or ``None`` if never.

        :meth:`~IWritePauseRepository.set_hub_paused` is only called after a successful hub
        round trip (``runner/loop/steps.py``), so its ``updated_at`` **is** the last-successful-
        contact instant — no separate fact needed (``bzh:facts-not-status``)."""
        ...

    def hub_paused(self, runner_id: str) -> bool:
        """The last hub pause brake value mirrored locally — consulted before claiming new work.

        Defaults False when it has never been synced (a fresh runner claims freely until it
        first hears otherwise)."""
        ...

    def local_paused(self, runner_id: str) -> bool:
        """This runner's own brake, derived from the newest local pause fact.

        Distinct from ``hub_paused``: it blocks every spawn site, not claims alone.
        Defaults False when the operator has never set it."""
        ...

    def local_pause_reason(self, runner_id: str) -> str | None:
        """The newest local pause fact's own reason — ``None`` on a plain
        operator pause, or when the brake has never been set. Read independently of
        :meth:`local_paused` so a caller decides for itself whether to consult it."""
        ...

    def last_daemon_liveness(self) -> datetime | None:
        """When the runner was last known alive, or ``None`` if it never ticked.

        The crash-time reference startup recovery classifies staleness against, stamped
        each tick, so the newest value is when the daemon died to within one tick."""
        ...

    def pause_parked_lease_ids(self) -> set[str]:
        """Leases dormant on an operator pause — a pause-park fact with no later
        pause-resume at or after it and no closure of its lease.

        A hub-terminal chunk closes its lease, and the lease closure closes its park.

        The pause-park half of
        :meth:`~blizzard.runner.leases.asks.IReadAskRepository.parked_lease_ids`'s union."""
        ...

    def open_pause_parks(self) -> dict[str, PausePark]:
        """Every open pause park by lease id — :meth:`pause_parked_lease_ids`'s leases, each with
        its ``parked_at`` and the elicitation its interrupt signalled. Hoisted once
        per tick (``bzh:bulk-reconstitution``) for the teardown ADVANCE completes over later
        ticks. A lease re-parked across a crash reads its newest park. A park closes with its
        lease: a hub-terminal chunk closes the lease, and the closed lease's park drops out of
        this read from then on."""
        ...


class IWritePauseRepository(IReadPauseRepository, Protocol):
    """Read-write pause-brake and daemon-liveness store — held only by the domain."""

    def record_daemon_liveness(self, *, runner_id: str, alive_at: datetime) -> None:
        """Stamp the runner as alive at ``alive_at`` — the tick's liveness beat.

        Upserted, one row per runner: only the newest instant matters, and it is the crash-time
        reference startup recovery reads back via :meth:`last_daemon_liveness`."""
        ...

    def set_hub_paused(self, runner_id: str, *, paused: bool, at: datetime) -> None:
        """Mirror the hub's pause brake locally (upsert) — read back before claiming new work."""
        ...

    def record_local_pause(
        self,
        runner_id: str,
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


class PauseService:
    """Composition-root-wired: the pause store, the clock, and the optional event
    publisher."""

    def __init__(
        self, store: IWritePauseRepository, clock: IClock, *, events: IRunnerEventPublisher | None = None
    ) -> None:
        self._store = store
        self._clock = clock
        self._events = events

    def set_local_pause(self, runner_id: str, *, paused: bool, by: str) -> None:
        """Set this runner's own pause brake and its upward report, atomically — or write
        nothing when :meth:`LocalBrake.set_by_operator` finds it already in the requested state.

        The brake and its hub-bound report are one write: mirroring runs hub→runner only, so a brake
        never reported up would never be repaired (``tests/test_ingest_and_pause_verbs.py``)."""
        fact = self._brake(runner_id).set_by_operator(paused=paused, by=by, at=self._clock.now())
        if fact is not None:
            self._write(runner_id, fact)

    def engage(self, runner_id: str, *, by: str, reason: str) -> None:
        """Engage the local brake with a durable reason — once (:meth:`LocalBrake.engage`).
        The operator's clear (:meth:`set_local_pause` with ``paused=False``) is the only lift."""
        fact = self._brake(runner_id).engage(by=by, reason=reason, at=self._clock.now())
        if fact is not None:
            self._write(runner_id, fact)

    def _brake(self, runner_id: str) -> LocalBrake:
        paused = self._store.local_paused(runner_id)
        return LocalBrake(paused=paused, reason=self._store.local_pause_reason(runner_id) if paused else None)

    def _write(self, runner_id: str, fact: LocalPauseFact) -> None:
        payload: dict[str, object] = {"runner_id": runner_id, "by": fact.by, "at": iso_utc(fact.at)}
        if fact.reason is not None:
            payload["reason"] = fact.reason
        seq = self._store.record_local_pause(
            runner_id,
            paused=fact.paused,
            at=fact.at,
            by=fact.by,
            report_kind=fact.report_kind,
            report_payload=json.dumps(payload),
            reason=fact.reason,
        )
        if self._events is not None:
            self._events.publish_fact_changed(seq=seq, kind=fact.report_kind, chunk_id=None, lease_id=None)
