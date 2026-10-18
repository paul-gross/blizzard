"""Lease staleness and derived lease state (``bzh:domain-core``).

The one owner of "when does a live worker read as stalled" — two copies of that
predicate would let one reader say ``running`` while another reaps the same lease. Also
holds the read model, which derives state from facts at read time
(``bzh:facts-not-status``). Stdlib and seam Protocols only."""

from __future__ import annotations

from collections.abc import Container, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.leases import LeaseClosureReason, LeaseDisplayStatus, LeaseState
from blizzard.foundation.roles import domain_model
from blizzard.foundation.store.utc import as_utc
from blizzard.runner.environments.repository import EnvBinding, IReadEnvironmentRepository, group_bindings_by_chunk
from blizzard.runner.leases.asks import IReadAskRepository
from blizzard.runner.leases.elicitation import IReadElicitationRepository
from blizzard.runner.leases.escalations import ParkedEscalation
from blizzard.runner.leases.liveness import IReadLeaseLivenessRepository, LeaseLivenessFacts
from blizzard.runner.leases.model import Lease
from blizzard.runner.leases.overload import IReadOverloadRepository, backing_off_facts
from blizzard.runner.leases.record import IReadLeaseRecordRepository

#: Deliberately **conservative**: bounded below by the longest tool call a healthy worker makes.
HEARTBEAT_STALENESS_THRESHOLD = timedelta(hours=1)

#: A **list-length affordance**, not a retention policy.
RECENT_LEASE_LIMIT = 20


@domain_model
@dataclass(frozen=True)
class Liveness:
    """A lease's staleness baseline: the newest of its heartbeat, its spawn, and its mint.

    ``max`` over all three rather than a chain, so a worker respawned into an old lease
    reads fresh for **every** spawn generation, not just the first."""

    last_activity: datetime

    @classmethod
    def of(cls, lease: Lease, *, heartbeat: datetime | None, spawn: datetime | None) -> Liveness:
        """The baseline from the lease's mint and its already-read activity facts. ``None``
        is a meaningful value for either fact — a lease that has never beaten, or never
        spawned — and leaves the mint (or the other fact) as the baseline."""
        facts = (heartbeat, spawn)
        return cls(max([as_utc(lease.created_at), *(as_utc(fact) for fact in facts if fact is not None)]))

    def stale(self, now: datetime, *, threshold: timedelta = HEARTBEAT_STALENESS_THRESHOLD) -> bool:
        """True iff the baseline is older than ``threshold`` as of ``now``."""
        return now - as_utc(self.last_activity) > threshold


# --- Derived lease state — the panel's read model ----------------


def lease_display_status(state: LeaseState, *, takeover: bool, escalated: bool, asking: bool) -> LeaseDisplayStatus:
    """What the panel shows for a lease of a chunk with these open human facts.

    An open takeover outranks everything (a human is in the session *now*), then an
    escalation (blocked on a human), then an open ask (waiting on one), then the lease's
    own ``state``. Pure — the chunk's facts are read once by the caller."""
    if takeover:
        return LeaseDisplayStatus.HUMAN_IN_SESSION
    if escalated:
        return LeaseDisplayStatus.NEEDS_HUMAN
    if asking:
        return LeaseDisplayStatus.WAITING_ASK
    return LeaseDisplayStatus(state)


@domain_model
@dataclass(frozen=True)
class ChunkHumanFacts:
    """The chunks that currently carry an open takeover, escalation, or ask."""

    takeover: frozenset[str] = frozenset()
    escalated: frozenset[str] = frozenset()
    asking: frozenset[str] = frozenset()

    def of_chunk(self, chunk_id: str) -> OpenHumanFacts:
        return OpenHumanFacts(
            takeover=chunk_id in self.takeover, escalated=chunk_id in self.escalated, asking=chunk_id in self.asking
        )


@domain_model
@dataclass(frozen=True)
class OpenHumanFacts:
    """Whether one chunk has an open takeover, escalation, or ask."""

    takeover: bool = False
    escalated: bool = False
    asking: bool = False

    def display_status(self, state: LeaseState) -> LeaseDisplayStatus:
        return lease_display_status(state, takeover=self.takeover, escalated=self.escalated, asking=self.asking)


NO_HUMAN_FACTS = OpenHumanFacts()


@domain_model
@dataclass(frozen=True)
class LeaseActivity:
    """An active lease with the facts its state derives from, plus its binding — the panel's read model.

    A closed lease is a :class:`ClosedLeaseActivity` instead, so it cannot carry liveness facts."""

    lease: Lease
    parked: bool
    alive: bool
    stale: bool
    backing_off: bool = False
    environment_id: str | None = None
    workdir: str | None = None
    last_heartbeat_at: datetime | None = None
    human: OpenHumanFacts = NO_HUMAN_FACTS

    @classmethod
    def of(
        cls,
        lease: Lease,
        *,
        facts: LeaseLivenessFacts | None,
        parked_ids: Container[str],
        backing_off: Container[str],
        bindings: Sequence[EnvBinding],
        alive: bool,
        now: datetime,
        stale_after: timedelta = HEARTBEAT_STALENESS_THRESHOLD,
        human: OpenHumanFacts = NO_HUMAN_FACTS,
    ) -> LeaseActivity:
        """Assemble ``lease``'s activity from facts already read: its liveness facts (``None``
        when it has none yet), the parked and backing-off lease ids, its chunk's held bindings, and whether its process probes alive."""
        heartbeat = facts.latest_heartbeat if facts is not None else None
        liveness = Liveness.of(lease, heartbeat=heartbeat, spawn=facts.latest_spawn if facts is not None else None)
        binding = bindings[0] if bindings else None
        return cls(
            lease=lease,
            parked=lease.lease_id in parked_ids,
            alive=alive,
            stale=liveness.stale(now, threshold=stale_after),
            backing_off=lease.lease_id in backing_off,
            environment_id=binding.environment_id if binding else None,
            workdir=binding.workdir if binding else None,
            last_heartbeat_at=heartbeat,
            human=human,
        )

    @property
    def state(self) -> LeaseState:
        """The lease's state, derived from the resolved facts — pure, no store, no I/O."""
        return self._derive_state()

    @property
    def display_status(self) -> LeaseDisplayStatus:
        return self.human.display_status(self.state)

    def _derive_state(self) -> LeaseState:
        """Apply the state precedence — a plain method, so mutation testing reaches it.

        ``parked`` outranks ``stale`` because parking stops the reap clock; ``backing-off``
        ranks below ``parked`` but above ``spawning``, or its exited worker misreads as ``exited``."""
        if self.parked:
            return "parked"
        if self.backing_off:
            return "backing-off"
        if self.lease.pid is None or self.lease.session_id is None:
            return "spawning"
        if not self.alive:
            return "exited"
        if self.stale:
            return "stale"
        return "running"


@domain_model
@dataclass(frozen=True)
class ClosedLeaseActivity:
    """A closed lease and its closure fact — no liveness facts, no binding (long released)."""

    lease: Lease
    closed_at: datetime
    closure_reason: str
    human: OpenHumanFacts = NO_HUMAN_FACTS

    @property
    def state(self) -> LeaseState:
        return "closed"

    @property
    def display_status(self) -> LeaseDisplayStatus:
        return self.human.display_status(self.state)

    @property
    def closure_healthy(self) -> bool:
        """Whether the closure was the node-step completing and the chunk moving on."""
        return LeaseClosureReason.is_healthy(self.closure_reason)


class IProcessProbe(Protocol):
    """The one process-liveness read this service needs.

    The domain declares the seam it needs (``bzh:dependency-inversion``), satisfied
    structurally — no shared base class."""

    def is_alive(self, pid: int, process_start_time: str) -> bool: ...


class IOpenTakeoverChunks(Protocol):
    """The one takeover read the lease display needs."""

    def open_takeover_chunk_ids(self) -> set[str]: ...


class IOpenEscalations(Protocol):
    """The one escalation read the lease display needs."""

    def open_escalations(self) -> list[ParkedEscalation]: ...


class LocalLeaseService:
    """Derive every active lease's state at read time — the panel's list.

    A status the store never stores. Spans leases, asks (parked) and environments
    (bindings)."""

    def __init__(
        self,
        clock: IClock,
        process: IProcessProbe,
        *,
        lease_record: IReadLeaseRecordRepository,
        liveness: IReadLeaseLivenessRepository,
        asks: IReadAskRepository,
        environments: IReadEnvironmentRepository,
        overload: IReadOverloadRepository,
        elicitations: IReadElicitationRepository,
        takeovers: IOpenTakeoverChunks,
        escalations: IOpenEscalations,
        stale_after: timedelta = HEARTBEAT_STALENESS_THRESHOLD,
        recent_limit: int = RECENT_LEASE_LIMIT,
    ) -> None:
        self._lease_record = lease_record
        self._liveness = liveness
        self._asks = asks
        self._environments = environments
        self._overload = overload
        self._elicitations = elicitations
        self._takeovers = takeovers
        self._escalations = escalations
        self._clock = clock
        self._process = process
        self._stale_after = stale_after
        self._recent_limit = recent_limit

    def list_active(self) -> list[LeaseActivity]:
        """Every active lease, joined with its binding and derived state.

        The reported heartbeat and the staleness baseline share one bulk
        :meth:`~IReadLeaseLivenessRepository.liveness_facts` read; bindings come from one
        :meth:`~IReadEnvironmentRepository.held_bindings` read — no per-lease reads."""
        now = self._clock.now()
        human = self._human_facts()
        parked = self._asks.parked_lease_ids()
        backing_off = backing_off_facts(self._overload, self._liveness, self._elicitations)
        leases = self._lease_record.list_active_leases()
        facts_by_lease = self._liveness.liveness_facts([lease.lease_id for lease in leases])
        bindings_by_chunk = group_bindings_by_chunk(self._environments.held_bindings())
        return [
            LeaseActivity.of(
                lease,
                facts=facts_by_lease.get(lease.lease_id),
                parked_ids=parked,
                backing_off=backing_off,
                bindings=bindings_by_chunk.get(lease.chunk_id, []),
                alive=self._is_alive(lease),
                now=now,
                stale_after=self._stale_after,
                human=human.of_chunk(lease.chunk_id),
            )
            for lease in leases
        ]

    def list_recent(self) -> list[LeaseActivity | ClosedLeaseActivity]:
        """Active leases, then the most recently closed — the panel's list.

        Every active lease first — unbounded, so a long-running agent is never crowded
        out — then up to ``recent_limit`` closed leases, newest first."""
        return [*self.list_active(), *self._list_closed()]

    def _list_closed(self) -> list[ClosedLeaseActivity]:
        """The recent-closed half of :meth:`list_recent` — no probe, no heartbeat read.

        A closed lease's pid may have been reused, so a pid read here would be actively
        misleading. Bindings are already released."""
        human = self._human_facts()
        return [
            ClosedLeaseActivity(
                lease=record.lease,
                closed_at=record.closed_at,
                closure_reason=record.reason,
                human=human.of_chunk(record.lease.chunk_id),
            )
            for record in self._lease_record.list_closed_leases(self._recent_limit)
        ]

    def _human_facts(self) -> ChunkHumanFacts:
        """The open takeover, escalation, and ask chunks — one bulk read each, never per lease."""
        return ChunkHumanFacts(
            takeover=frozenset(self._takeovers.open_takeover_chunk_ids()),
            escalated=frozenset(e.chunk_id for e in self._escalations.open_escalations()),
            asking=frozenset(a.chunk_id for a in self._asks.open_asks()),
        )

    def _is_alive(self, lease: Lease) -> bool:
        if lease.pid is None:
            return False  # spawning — `LeaseActivity.state` short-circuits before this matters
        return self._process.is_alive(lease.pid, lease.process_start_time or "")
