"""Lease staleness and derived lease state (``bzh:domain-core``).

REAP (``runner/loop/steps.py``) and the panel's derived lease state
(:class:`LocalLeaseService`, issue #28) must agree on exactly when a live worker reads
as stalled — two independent copies of this predicate would let the panel say
``running`` while REAP is reaping the same lease. This module is that predicate's one
owner; both callers import it rather than re-deriving it.

It also holds the panel's read model: :func:`derive_lease_state` — a pure function
mirroring :func:`blizzard.hub.domain.registry.derive_online` — and
:class:`LocalLeaseService`, the domain service that reads the store and process probe
and returns each active lease's derived state (``bzh:facts-not-status``): no status
column is read or written, the state is computed at read time from facts.

This layer imports no FastAPI, no SQLAlchemy, no click — only stdlib and the seam
Protocols (``bzh:dependency-inversion``) it reads leases, heartbeats, and process
liveness through. It does not import from ``runner/loop/`` — :class:`IProcessProbe`
below is a narrower Protocol this module owns for the one method it needs
(structural typing: the loop's ``LinuxProcessProbe``/``FakeProbe`` satisfy it with no
shared base class), not a re-export of the loop's own seam.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.store.utc import as_utc
from blizzard.runner.store.repository import EnvBindingRecord, IReadRunnerStore, LeaseRecord

__all__ = [
    "HEARTBEAT_STALENESS_THRESHOLD",
    "IProcessProbe",
    "LeaseActivity",
    "LeaseState",
    "LocalLeaseService",
    "as_utc",
    "derive_lease_state",
    "is_heartbeat_stale",
]

#: REAP's staleness threshold (design/runner/loop.md). Deliberately **conservative**:
#: heartbeats ride tool calls, so the threshold is bounded below by the longest tool
#: call a healthy worker makes — one long test run must never read as a stall. A live
#: worker whose last heartbeat is older than this has stopped making tool calls and is
#: reaped as stalled (D-078). ~1h; the open-question constant (decisions/open-questions.md).
HEARTBEAT_STALENESS_THRESHOLD = timedelta(hours=1)

#: The panel's derived state (design/runner/loop.md, issue #28) — one of five, computed
#: at read time and never stored (``bzh:facts-not-status``).
LeaseState = Literal["running", "stale", "parked", "spawning", "exited"]


def is_heartbeat_stale(store: IReadRunnerStore, lease: LeaseRecord, now: datetime) -> bool:
    """True iff the lease's last activity is older than the staleness threshold.

    Last activity is the newest heartbeat, or — before the worker's first tool call —
    the lease's own creation instant, so a freshly spawned worker is never read as
    stalled inside the threshold window.
    """
    last = store.latest_heartbeat(lease.lease_id) or lease.created_at
    return _staleness_exceeded(last, now, threshold=HEARTBEAT_STALENESS_THRESHOLD)


def _staleness_exceeded(last_heartbeat: datetime, now: datetime, *, threshold: timedelta) -> bool:
    """Pure comparison: True iff ``last_heartbeat`` is older than ``threshold`` as of ``now``.

    Split out of :func:`is_heartbeat_stale` so :class:`LocalLeaseService` can reuse the
    exact comparison after doing its own store read, without either copying the rule or
    forcing :func:`derive_lease_state` to take a store (``bzh:domain-core``). REAP's
    behavior is unchanged: :func:`is_heartbeat_stale` still resolves the same ``last``
    and calls this with the same module threshold, so this is a shape refactor, not a
    behavior change.
    """
    return now - as_utc(last_heartbeat) > threshold


# ``as_utc`` re-exported from ``foundation/store/utc.py`` (issue #28, ``bzh:utc-instants``):
# kept importable from here — ``runner/api/leases.py`` and existing callers depend on the
# name at this path — but this module no longer owns its own copy. Every store column is
# ``UtcDateTime``-typed, so the coercion here is a defensive no-op, kept because this
# module's inputs are not guaranteed to come from the store (``bzh:domain-core``).


# --------------------------------------------------------------------------- #
# Derived lease state — the panel's read model (issue #28)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LeaseActivity:
    """A lease with its derived state and joined binding facts — the panel's read model.

    Mirrors :class:`blizzard.hub.domain.registry.RunnerLiveness`: it nests the raw
    :class:`LeaseRecord` alongside what the store never stores. ``environment_id`` /
    ``workdir`` come from the chunk's binding join; ``last_heartbeat_at`` is the newest
    heartbeat, or ``None`` if the lease has never beaten (the Phase 3 wire model maps
    this facts-plus-derivation shape onto ``LeaseView``).
    """

    lease: LeaseRecord
    state: LeaseState
    environment_id: str | None
    workdir: str | None
    last_heartbeat_at: datetime | None


def derive_lease_state(lease: LeaseRecord, *, is_parked: bool, is_alive: bool, is_stale: bool) -> LeaseState:
    """Derive a lease's state from precomputed facts — pure, no store, no I/O.

    Precedence (design/runner/loop.md, issue #28) — order is the point:

    1. **parked** — a park fact with no later resume ([ask-answer.md]); the reap clock
       is stopped, so a parked-and-stale lease still reads ``parked``, never ``stale``.
    2. **spawning** — ``pid``/``session_id`` unset: minted at FILL, spawn-return not yet
       recorded (D-092); a spawning lease has no meaningful heartbeat, so this wins over
       ``is_stale`` regardless of how old its heartbeat baseline would compute.
    3. **exited** — a live-pid check came back false; exit is the done-declaration
       (D-055), awaiting ADVANCE's judgement, not dead.
    4. **stale** — alive, but the caller's staleness read (REAP's own predicate, via
       :func:`is_heartbeat_stale` / :func:`_staleness_exceeded`) says the heartbeat is
       too old.
    5. **running** — otherwise.

    ``is_alive`` and ``is_stale`` are facts the caller (:class:`LocalLeaseService`)
    resolved beforehand — a process-probe read and a heartbeat read, respectively —
    exactly the seam :func:`is_heartbeat_stale` already draws between the store read
    and the pure comparison.
    """
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

    Narrower than the loop's full seam (``runner/loop/process.py``'s ``IProcessProbe``,
    which also carries ``start_time``/``kill``) so this domain module can own its own
    Protocol without importing across the ``runner/loop`` boundary (``bzh:domain-core``,
    ``bzh:dependency-inversion``: the domain declares the seam it needs). Both the
    loop's ``LinuxProcessProbe`` and ``tests/runner_fakes.py``'s ``FakeProbe`` satisfy
    this structurally — Protocol typing needs no shared base class.
    """

    def is_alive(self, pid: int, process_start_time: str) -> bool: ...


class LocalLeaseService:
    """Derive every active lease's state at read time — the panel's list (issue #28).

    Mirrors :class:`blizzard.hub.domain.registry.FleetService`: a status the store
    never stores, computed here from facts plus the injected clock and process probe.
    Holds only :class:`IReadRunnerStore` (``bzh:repository-split``) — this is a read
    path, so it is safe for a controller to hold this service directly
    (``bzh:controller-read-only``).
    """

    def __init__(
        self,
        store: IReadRunnerStore,
        clock: IClock,
        process: IProcessProbe,
        stale_after: timedelta = HEARTBEAT_STALENESS_THRESHOLD,
    ) -> None:
        self._store = store
        self._clock = clock
        self._process = process
        self._stale_after = stale_after

    def list_active(self) -> list[LeaseActivity]:
        """Every active lease, joined with its binding and derived state.

        Reads ``parked_lease_ids()`` once (not per-lease) and, per lease (N+1 bounded
        by ``MAX_AGENTS``, ~4 — accepted rather than extending the repository, which
        would be speculative): ``latest_heartbeat`` for the staleness read and the
        panel's heartbeat-age column, and ``bindings_for_chunk`` for the environment
        join.
        """
        now = self._clock.now()
        parked = self._store.parked_lease_ids()
        activities: list[LeaseActivity] = []
        for lease in self._store.list_active_leases():
            last_heartbeat = self._store.latest_heartbeat(lease.lease_id)
            baseline = last_heartbeat or lease.created_at
            state = derive_lease_state(
                lease,
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
                )
            )
        return activities

    def _is_alive(self, lease: LeaseRecord) -> bool:
        if lease.pid is None:
            return False  # spawning — derive_lease_state short-circuits before this matters
        return self._process.is_alive(lease.pid, lease.process_start_time or "")

    def _first_binding(self, chunk_id: str) -> EnvBindingRecord | None:
        bindings = self._store.bindings_for_chunk(chunk_id)
        return bindings[0] if bindings else None
