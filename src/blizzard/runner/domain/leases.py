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
    "RECENT_LEASE_LIMIT",
    "IProcessProbe",
    "LeaseActivity",
    "LeaseState",
    "LocalLeaseService",
    "as_utc",
    "derive_lease_state",
    "is_heartbeat_stale",
]

#: REAP's staleness threshold. Deliberately **conservative**:
#: heartbeats ride tool calls, so the threshold is bounded below by the longest tool
#: call a healthy worker makes — one long test run must never read as a stall. A live
#: worker whose last heartbeat is older than this has stopped making tool calls and is
#: reaped as stalled. ~1h; the open-question constant.
HEARTBEAT_STALENESS_THRESHOLD = timedelta(hours=1)

#: The panel's recently-closed-lease list length (issue #29) — a
#: **list-length affordance**, not a retention policy: it bounds how many closed rows
#: :meth:`LocalLeaseService.list_recent` returns, not how long a closure fact or its
#: transcript lives on disk (a separate, undecided product question). Mirrors
#: :data:`HEARTBEAT_STALENESS_THRESHOLD` as a documented module constant rather than a
#: config knob, and is injectable the same way (``LocalLeaseService(..., recent_limit=…)``).
#: ``MAX_AGENTS`` is ~4, so 20 closed leases covers several hours of fleet activity.
RECENT_LEASE_LIMIT = 20

#: The panel's derived state (issue #28; ``closed`` added issue #29)
#: — one of six, computed at read time and never stored (``bzh:facts-not-status``).
LeaseState = Literal["running", "stale", "parked", "spawning", "exited", "closed"]


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
    this facts-plus-derivation shape onto ``LeaseView``). ``closed_at`` / ``closure_reason``
    (issue #29) are ``None`` iff the lease is active — a closed lease also carries
    ``environment_id is None`` and ``workdir is None``, because its bindings are always
    released by the time closure is recorded (``bindings_for_chunk`` returns only
    *unreleased* bindings); there is no fact left to reconstruct them from.
    """

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

    Precedence (issue #28; ``closed`` added issue #29) — order is
    the point:

    1. **closed** — a closure fact exists (``record_closure``).
       **Highest precedence**, checked before ``is_alive``: a closed lease's
       ``pid`` may have been reused by an unrelated process, so a live-pid probe can
       false-positive and claim a finished agent is still running. Closure is the
       terminal fact and must win over everything else, the same way ``parked`` already
       wins over ``stale`` below.
    2. **parked** — a park fact with no later resume; the reap clock
       is stopped, so a parked-and-stale lease still reads ``parked``, never ``stale``.
    3. **spawning** — ``pid``/``session_id`` unset: minted at FILL, spawn-return not yet
       recorded; a spawning lease has no meaningful heartbeat, so this wins over
       ``is_stale`` regardless of how old its heartbeat baseline would compute.
    4. **exited** — a live-pid check came back false; exit is the done-declaration,
       awaiting ADVANCE's judgement, not dead.
    5. **stale** — alive, but the caller's staleness read (REAP's own predicate, via
       :func:`is_heartbeat_stale` / :func:`_staleness_exceeded`) says the heartbeat is
       too old.
    6. **running** — otherwise.

    ``is_closed``, ``is_alive``, and ``is_stale`` are facts the caller
    (:class:`LocalLeaseService`) resolved beforehand — a closure-fact read, a
    process-probe read, and a heartbeat read, respectively — exactly the seam
    :func:`is_heartbeat_stale` already draws between the store read and the pure
    comparison. :meth:`LocalLeaseService.list_active` always passes ``is_closed=False``
    (its source read, ``list_active_leases``, is unclosed *by definition*), so this
    addition is zero blast radius on the existing five-state path.
    """
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
        recent_limit: int = RECENT_LEASE_LIMIT,
    ) -> None:
        self._store = store
        self._clock = clock
        self._process = process
        self._stale_after = stale_after
        self._recent_limit = recent_limit

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

        Ordering is server-owned (one owner) so the UI just renders: every active
        lease first (:meth:`list_active`'s own order — unbounded, so a long-running
        agent can never be crowded out), then up to ``recent_limit`` closed leases,
        newest-closed first (:meth:`IReadRunnerStore.list_closed_leases`'s own order).
        A single ``list_recent(limit)`` read merging both would let recently-closed
        leases crowd an older active one out of a shared cap — this keeps the active
        side unbounded-correct and only the closed side bounded.
        """
        return self.list_active() + self._list_closed()

    def _list_closed(self) -> list[LeaseActivity]:
        """The recent-closed half of :meth:`list_recent` — no probe, no heartbeat read.

        ``closed`` wins :func:`derive_lease_state`'s precedence unconditionally, so the
        process-liveness and staleness reads :meth:`list_active` makes would be wasted
        I/O here — and the pid read would be actively misleading (a closed lease's pid
        may have been reused by an unrelated process). ``environment_id``/``workdir``
        are ``None``: a closed lease's bindings are always released by the time closure
        is recorded, so there is no unreleased binding left to join (issue #29).
        """
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
