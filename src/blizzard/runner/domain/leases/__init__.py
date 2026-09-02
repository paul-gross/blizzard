"""Lease staleness and derived lease state (``bzh:domain-core``).

The one owner of "when does a live worker read as stalled" — two copies of that
predicate would let one reader say ``running`` while another reaps the same lease. Also
holds the read model, which derives state from facts at read time
(``bzh:facts-not-status``). Stdlib and seam Protocols only.

The four repository seams (``record``, ``session``, ``liveness``, ``resume_intent``) each
declare their own read/write Protocol pair in their own module, mirroring the store
adapters underneath; this package re-exports them so every existing caller keeps importing
from ``blizzard.runner.domain.leases``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.store.utc import as_utc
from blizzard.runner.domain.leases.liveness import (
    IReadLeaseLivenessRepository,
    IWriteLeaseLivenessRepository,
)
from blizzard.runner.domain.leases.record import (
    IReadLeaseRecordRepository,
    IWriteLeaseRecordRepository,
)
from blizzard.runner.domain.leases.resume_intent import (
    IReadLeaseResumeIntentRepository,
    IWriteLeaseResumeIntentRepository,
)
from blizzard.runner.domain.leases.session import (
    IReadLeaseSessionRepository,
    IWriteLeaseSessionRepository,
)
from blizzard.runner.environments.repository import EnvBindingRecord

if TYPE_CHECKING:
    # Deferred: ``runner/stores.py`` composes this module's own Protocol.
    from blizzard.runner.stores import RunnerReadStores

__all__ = [
    "HEARTBEAT_STALENESS_THRESHOLD",
    "RECENT_LEASE_LIMIT",
    "ClosedLeaseRecord",
    "IProcessProbe",
    "IReadLeaseLivenessRepository",
    "IReadLeaseRecordRepository",
    "IReadLeaseResumeIntentRepository",
    "IReadLeaseSessionRepository",
    "IWriteLeaseLivenessRepository",
    "IWriteLeaseRecordRepository",
    "IWriteLeaseResumeIntentRepository",
    "IWriteLeaseSessionRepository",
    "LeaseActivity",
    "LeaseRecord",
    "LeaseState",
    "Liveness",
    "LocalLeaseService",
    "NewLease",
    "PoolHead",
    "as_utc",
]


@dataclass(frozen=True)
class NewLease:
    """A node-step lease at mint — before the worker exists."""

    lease_id: str
    chunk_id: str
    graph_id: str
    node_id: str
    node_name: str
    epoch: int
    runner_id: str
    retries_max: int
    created_at: datetime
    # What session this attempt runs and under what configuration (issue #144), stamped on
    # the mint's own `lease_context` insert. `None` means *unknown*, never a value.
    session_name: str | None = None
    resolved_model: str | None = None
    resolved_effort: str | None = None
    resolved_compaction_window: str | None = None


@dataclass(frozen=True)
class PoolHead:
    """A named session pool's current head (issue #144). ``resolved_model``/
    ``resolved_effort`` are the head's own **stamps**, not a fresh resolution; ``None``
    on either means *unknown*, never a value."""

    session_id: str
    lease_id: str
    resolved_model: str | None
    resolved_effort: str | None


@dataclass(frozen=True)
class LeaseRecord:
    """A lease joined with its node context — the loop's per-attempt fact.

    ``pid`` / ``process_start_time`` / ``session_id`` are ``None`` until spawn-return."""

    lease_id: str
    chunk_id: str
    graph_id: str
    node_id: str
    node_name: str
    epoch: int
    runner_id: str
    retries_max: int
    created_at: datetime
    # This attempt's session stamps, read back (issue #144). `None` on any of the three
    # means *unknown*, never a value.
    session_name: str | None = None
    resolved_model: str | None = None
    resolved_effort: str | None = None
    resolved_compaction_window: str | None = None
    pid: int | None = None
    process_start_time: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class ClosedLeaseRecord:
    """A lease joined with its closure fact — the panel's recent-history read (issue #29).

    ``reason`` is the closure vocabulary: ``transitioned`` | ``reaped`` | ``failed`` |
    ``escalated`` | ``parked`` | ``released``."""

    lease: LeaseRecord
    reason: str
    closed_at: datetime


#: Deliberately **conservative**: heartbeats ride tool calls, so this is bounded below
#: by the longest tool call a healthy worker makes.
HEARTBEAT_STALENESS_THRESHOLD = timedelta(hours=1)

#: A **list-length affordance**, not a retention policy: it bounds how many closed rows
#: are returned, never how long a closure fact lives (issue #29).
RECENT_LEASE_LIMIT = 20

#: The panel's derived state (issue #28; ``closed`` added issue #29)
#: — one of six, computed at read time and never stored (``bzh:facts-not-status``).
LeaseState = Literal["running", "stale", "parked", "spawning", "exited", "closed"]


class _Unread:
    """The "caller did not supply a heartbeat" sentinel for :meth:`Liveness.of`.

    A distinct type rather than ``None``, because ``None`` is itself a meaningful value
    there — a lease that has never beaten — and the two must not collapse."""


_UNREAD = _Unread()


@dataclass(frozen=True)
class Liveness:
    """A lease's staleness baseline: the newest of its heartbeat, its spawn, and its mint.

    ``max`` over all three rather than a chain, so a worker respawned into an old lease
    reads fresh for **every** spawn generation, not just the first (issue #150)."""

    last_activity: datetime

    @classmethod
    def of(
        cls, store: IReadLeaseLivenessRepository, lease: LeaseRecord, *, heartbeat: datetime | None | _Unread = _UNREAD
    ) -> Liveness:
        """Read the lease's activity facts, taking an already-read ``heartbeat`` if offered."""
        beat = store.latest_heartbeat(lease.lease_id) if isinstance(heartbeat, _Unread) else heartbeat
        facts = (beat, store.latest_spawn(lease.lease_id))
        return cls(max([as_utc(lease.created_at), *(as_utc(fact) for fact in facts if fact is not None)]))

    def stale(self, now: datetime, *, threshold: timedelta = HEARTBEAT_STALENESS_THRESHOLD) -> bool:
        """True iff the baseline is older than ``threshold`` as of ``now``."""
        return now - as_utc(self.last_activity) > threshold


# ``as_utc`` is re-exported: callers depend on the name at this path.


# --- Derived lease state — the panel's read model (issue #28) ----------------


@dataclass(frozen=True)
class LeaseActivity:
    """A lease with the facts its state derives from, plus its binding — the panel's read model.

    ``closed_at``/``closure_reason`` are ``None`` iff the lease is active; a closed one
    also carries no ``environment_id`` or ``workdir``, its bindings being long released."""

    lease: LeaseRecord
    closed: bool
    parked: bool
    alive: bool
    stale: bool
    environment_id: str | None = None
    workdir: str | None = None
    last_heartbeat_at: datetime | None = None
    closed_at: datetime | None = None
    closure_reason: str | None = None

    @property
    def state(self) -> LeaseState:
        """The lease's state, derived from the resolved facts — pure, no store, no I/O.

        The precedence is the point: ``closed`` outranks ``alive`` because a closed
        lease's pid may have been reused, and ``parked`` outranks ``stale`` because
        parking stops the reap clock."""
        if self.closed:
            return "closed"
        if self.parked:
            return "parked"
        if self.lease.pid is None or self.lease.session_id is None:
            return "spawning"
        if not self.alive:
            return "exited"
        if self.stale:
            return "stale"
        return "running"


class IProcessProbe(Protocol):
    """The one process-liveness read this service needs.

    The domain declares the seam it needs (``bzh:dependency-inversion``), satisfied
    structurally — no shared base class."""

    def is_alive(self, pid: int, process_start_time: str) -> bool: ...


class LocalLeaseService:
    """Derive every active lease's state at read time — the panel's list (issue #28).

    A status the store never stores. Spans leases, asks (parked) and environments
    (bindings), so it holds the :class:`~blizzard.runner.stores.RunnerReadStores` bundle
    (D4) — verified read-only over it, so it takes the narrowed bundle (blizzard#412)."""

    def __init__(
        self,
        stores: RunnerReadStores,
        clock: IClock,
        process: IProcessProbe,
        stale_after: timedelta = HEARTBEAT_STALENESS_THRESHOLD,
        recent_limit: int = RECENT_LEASE_LIMIT,
    ) -> None:
        self._stores = stores
        self._clock = clock
        self._process = process
        self._stale_after = stale_after
        self._recent_limit = recent_limit

    def list_active(self) -> list[LeaseActivity]:
        """Every active lease, joined with its binding and derived state.

        The reported heartbeat and the staleness baseline are different questions, but
        share one heartbeat read. The remaining per-lease N+1 is accepted."""
        now = self._clock.now()
        parked = self._stores.asks.parked_lease_ids()
        activities: list[LeaseActivity] = []
        for lease in self._stores.lease_record.list_active_leases():
            last_heartbeat = self._stores.liveness.latest_heartbeat(lease.lease_id)
            liveness = Liveness.of(self._stores.liveness, lease, heartbeat=last_heartbeat)
            alive = self._is_alive(lease)
            binding = self._first_binding(lease.chunk_id)
            activities.append(
                LeaseActivity(
                    lease=lease,
                    closed=False,
                    parked=lease.lease_id in parked,
                    alive=alive,
                    stale=liveness.stale(now, threshold=self._stale_after),
                    environment_id=binding.environment_id if binding else None,
                    workdir=binding.workdir if binding else None,
                    last_heartbeat_at=last_heartbeat,
                )
            )
        return activities

    def list_recent(self) -> list[LeaseActivity]:
        """Active leases, then the most recently closed — the panel's list (issue #29).

        Every active lease first — unbounded, so a long-running agent is never crowded
        out — then up to ``recent_limit`` closed leases, newest first."""
        return self.list_active() + self._list_closed()

    def _list_closed(self) -> list[LeaseActivity]:
        """The recent-closed half of :meth:`list_recent` — no probe, no heartbeat read.

        ``closed`` wins the precedence unconditionally, so a pid read here would be
        wasted and actively misleading. Bindings are already released (issue #29)."""
        return [
            LeaseActivity(
                lease=record.lease,
                closed=True,
                parked=False,
                alive=False,
                stale=False,
                closed_at=record.closed_at,
                closure_reason=record.reason,
            )
            for record in self._stores.lease_record.list_closed_leases(self._recent_limit)
        ]

    def _is_alive(self, lease: LeaseRecord) -> bool:
        if lease.pid is None:
            return False  # spawning — `LeaseActivity.state` short-circuits before this matters
        return self._process.is_alive(lease.pid, lease.process_start_time or "")

    def _first_binding(self, chunk_id: str) -> EnvBindingRecord | None:
        bindings = self._stores.environments.bindings_for_chunk(chunk_id)
        return bindings[0] if bindings else None
