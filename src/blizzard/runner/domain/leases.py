"""Lease staleness and derived lease state (``bzh:domain-core``).

The one owner of "when does a live worker read as stalled" — two copies of that
predicate would let one reader say ``running`` while another reaps the same lease. Also
holds the read model, which derives state from facts at read time
(``bzh:facts-not-status``). Stdlib and seam Protocols only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.store.utc import as_utc
from blizzard.runner.store.repository import EnvBindingRecord, IReadRunnerStore, LeaseRecord

__all__ = [
    "HEARTBEAT_STALENESS_THRESHOLD",
    "RECENT_LEASE_LIMIT",
    "IProcessProbe",
    "LeaseActivity",
    "LeaseState",
    "LocalLeaseService",
    "as_utc",
    "derive_lease_state",
    "is_heartbeat_stale",
    "last_activity",
]

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
    """The "caller did not supply a heartbeat" sentinel for :func:`last_activity`.

    A distinct type rather than ``None``, because ``None`` is itself a meaningful value
    there — a lease that has never beaten — and the two must not collapse."""


_UNREAD = _Unread()


def is_heartbeat_stale(store: IReadRunnerStore, lease: LeaseRecord, now: datetime) -> bool:
    """True iff the lease's last activity is older than the staleness threshold.

    See :func:`last_activity` for what "last activity" means."""
    return _staleness_exceeded(last_activity(store, lease), now, threshold=HEARTBEAT_STALENESS_THRESHOLD)


def last_activity(
    store: IReadRunnerStore, lease: LeaseRecord, *, heartbeat: datetime | None | _Unread = _UNREAD
) -> datetime:
    """A lease's staleness baseline: the newest of its heartbeat, its spawn, and its mint.

    A freshly spawned worker must never read as stalled inside the threshold window, for
    **every** spawn generation, not just the first (issue #150). ``max`` over all three
    rather than a chain: only the newest is "activity", floored at ``created_at``."""
    beat = store.latest_heartbeat(lease.lease_id) if isinstance(heartbeat, _Unread) else heartbeat
    facts = (beat, store.latest_spawn(lease.lease_id))
    return max([as_utc(lease.created_at), *(as_utc(fact) for fact in facts if fact is not None)])


def _staleness_exceeded(last_activity_at: datetime, now: datetime, *, threshold: timedelta) -> bool:
    """Pure comparison: True iff ``last_activity_at`` is older than ``threshold`` as of ``now``.

    Split out so a caller with its own store read reuses the exact comparison."""
    return now - as_utc(last_activity_at) > threshold


# ``as_utc`` is re-exported: callers depend on the name at this path.


# --- Derived lease state — the panel's read model (issue #28) ----------------


@dataclass(frozen=True)
class LeaseActivity:
    """A lease with its derived state and joined binding facts — the panel's read model.

    ``closed_at``/``closure_reason`` are ``None`` iff the lease is active; a closed one
    also carries no ``environment_id`` or ``workdir``, its bindings being long released."""

    lease: LeaseRecord
    state: LeaseState
    environment_id: str | None
    workdir: str | None
    last_heartbeat_at: datetime | None
    closed_at: datetime | None
    closure_reason: str | None


def derive_lease_state(
    lease: LeaseRecord, *, is_closed: bool, is_parked: bool, is_alive: bool, is_stale: bool
) -> LeaseState:
    """Derive a lease's state from precomputed facts — pure, no store, no I/O.

    The precedence is the point: ``closed`` outranks ``is_alive`` because a closed
    lease's pid may have been reused, and ``parked`` outranks ``stale`` because parking
    stops the reap clock. Every input is a fact the caller resolved beforehand."""
    if is_closed:
        return "closed"
    if is_parked:
        return "parked"
    if lease.pid is None or lease.session_id is None:
        return "spawning"
    if not is_alive:
        return "exited"
    if is_stale:
        return "stale"
    return "running"


class IProcessProbe(Protocol):
    """The one process-liveness read this service needs.

    The domain declares the seam it needs (``bzh:dependency-inversion``), satisfied
    structurally — no shared base class."""

    def is_alive(self, pid: int, process_start_time: str) -> bool: ...


class LocalLeaseService:
    """Derive every active lease's state at read time — the panel's list (issue #28).

    A status the store never stores. Holds only :class:`IReadRunnerStore`
    (``bzh:repository-split``), so a controller may hold this service directly."""

    def __init__(
        self,
        store: IReadRunnerStore,
        clock: IClock,
        process: IProcessProbe,
        stale_after: timedelta = HEARTBEAT_STALENESS_THRESHOLD,
        recent_limit: int = RECENT_LEASE_LIMIT,
    ) -> None:
        self._store = store
        self._clock = clock
        self._process = process
        self._stale_after = stale_after
        self._recent_limit = recent_limit

    def list_active(self) -> list[LeaseActivity]:
        """Every active lease, joined with its binding and derived state.

        The reported heartbeat and the staleness baseline are different questions, but
        share one heartbeat read. The remaining per-lease N+1 is accepted."""
        now = self._clock.now()
        parked = self._store.parked_lease_ids()
        activities: list[LeaseActivity] = []
        for lease in self._store.list_active_leases():
            last_heartbeat = self._store.latest_heartbeat(lease.lease_id)
            baseline = last_activity(self._store, lease, heartbeat=last_heartbeat)
            state = derive_lease_state(
                lease,
                is_closed=False,
                is_parked=lease.lease_id in parked,
                is_alive=self._is_alive(lease),
                is_stale=_staleness_exceeded(baseline, now, threshold=self._stale_after),
            )
            binding = self._first_binding(lease.chunk_id)
            activities.append(
                LeaseActivity(
                    lease=lease,
                    state=state,
                    environment_id=binding.environment_id if binding else None,
                    workdir=binding.workdir if binding else None,
                    last_heartbeat_at=last_heartbeat,
                    closed_at=None,
                    closure_reason=None,
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
                state=derive_lease_state(record.lease, is_closed=True, is_parked=False, is_alive=False, is_stale=False),
                environment_id=None,
                workdir=None,
                last_heartbeat_at=None,
                closed_at=record.closed_at,
                closure_reason=record.reason,
            )
            for record in self._store.list_closed_leases(self._recent_limit)
        ]

    def _is_alive(self, lease: LeaseRecord) -> bool:
        if lease.pid is None:
            return False  # spawning — derive_lease_state short-circuits before this matters
        return self._process.is_alive(lease.pid, lease.process_start_time or "")

    def _first_binding(self, chunk_id: str) -> EnvBindingRecord | None:
        bindings = self._store.bindings_for_chunk(chunk_id)
        return bindings[0] if bindings else None
