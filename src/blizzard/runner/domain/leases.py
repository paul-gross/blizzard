"""Lease staleness and derived lease state (``bzh:domain-core``).

REAP (``runner/loop/steps.py``) and the panel's derived lease state
(:class:`LocalLeaseService`, issue #28) must agree on exactly when a live worker reads
as stalled — two independent copies of this predicate would let the panel say
``running`` while REAP is reaping the same lease. This module is that predicate's one
owner; both callers import it rather than re-deriving it.

It also holds the panel's read model: :func:`derive_lease_state` and
:class:`LocalLeaseService`, which returns each active lease's state derived at read
time from facts (``bzh:facts-not-status``) — no status column is read or written.

This layer imports no FastAPI, no SQLAlchemy, no click — only stdlib and the seam
Protocols (``bzh:dependency-inversion``) it reads leases, heartbeats, and process
liveness through, including its own :class:`IProcessProbe` rather than the loop's.
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
    "last_activity",
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
#: transcript lives on disk (a separate, undecided product question).
#: ``MAX_AGENTS`` is ~4, so 20 closed leases covers several hours of fleet activity.
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

    See :func:`last_activity` for what "last activity" means — the read this and
    :class:`LocalLeaseService` share so REAP and the panel cannot drift apart.
    """
    return _staleness_exceeded(last_activity(store, lease), now, threshold=HEARTBEAT_STALENESS_THRESHOLD)


def last_activity(
    store: IReadRunnerStore, lease: LeaseRecord, *, heartbeat: datetime | None | _Unread = _UNREAD
) -> datetime:
    """A lease's staleness baseline: the newest of its heartbeat, its spawn, and its mint.

    The promise — *a freshly spawned worker is never read as stalled inside the threshold
    window* — has to hold for **every** spawn generation, not just the first (issue #150):
    heartbeats ride tool calls, so a resumed lease whose baseline was still its pre-park
    beat would be stale at birth and reaped seconds into a healthy first inference turn.
    Folding the newest ``lease_spawns`` row in closes that for every resume path that
    reuses a lease, with no new write and no new fact.

    Deliberately **not** a fabricated heartbeat row. Heartbeats are worker-originated —
    they prove the worker is making tool calls — and a runner-written beat would make
    the ``heartbeats`` table lie about worker activity while still needing backfilling
    at all four spawn sites. Deriving from the spawn fact keeps the facts honest.

    ``max`` over all three rather than a precedence chain: a lease can hold a heartbeat
    newer than its newest spawn (a worker beating away right now) or a spawn newer than
    its newest heartbeat (a just-resumed worker), and only the newer of the two is
    "activity". ``created_at`` is the floor for a lease that has neither.

    ``heartbeat`` lets a caller that has **already** read ``latest_heartbeat`` pass it in
    rather than have this issue the identical query a second time. The sentinel
    distinguishes "not supplied, go read it" from a supplied ``None`` (a lease that has
    genuinely never beaten), which a plain ``None`` default could not.
    """
    beat = store.latest_heartbeat(lease.lease_id) if isinstance(heartbeat, _Unread) else heartbeat
    facts = (beat, store.latest_spawn(lease.lease_id))
    return max([as_utc(lease.created_at), *(as_utc(fact) for fact in facts if fact is not None)])


def _staleness_exceeded(last_activity_at: datetime, now: datetime, *, threshold: timedelta) -> bool:
    """Pure comparison: True iff ``last_activity_at`` is older than ``threshold`` as of ``now``.

    Split out of :func:`is_heartbeat_stale` so :class:`LocalLeaseService` can reuse the
    exact comparison after doing its own store read, without copying the rule or forcing
    :func:`derive_lease_state` to take a store (``bzh:domain-core``).
    """
    return now - as_utc(last_activity_at) > threshold


# ``as_utc`` re-exported from ``foundation/store/utc.py`` (issue #28, ``bzh:utc-instants``):
# kept importable from here because callers depend on the name at this path. The coercion
# is defensive — this module's inputs are not guaranteed to come from the store
# (``bzh:domain-core``).


# --------------------------------------------------------------------------- #
# Derived lease state — the panel's read model (issue #28)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LeaseActivity:
    """A lease with its derived state and joined binding facts — the panel's read model.

    It nests the raw :class:`LeaseRecord` alongside what the store never stores.
    ``environment_id`` / ``workdir`` come from the chunk's binding join;
    ``last_heartbeat_at`` is the newest heartbeat, or ``None`` if the lease has never
    beaten. ``closed_at`` / ``closure_reason`` (issue #29) are ``None`` iff the lease is
    active — a closed lease also carries ``environment_id is None`` and ``workdir is
    None``, because its bindings are always released by the time closure is recorded.
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

    ``is_closed``, ``is_alive``, and ``is_stale`` are facts the caller resolved
    beforehand — a closure-fact read, a process-probe read, and a heartbeat read — which
    is what keeps this function pure (``bzh:domain-core``).
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

    Narrower than the loop's seam (``runner/loop/process.py``) so this domain module can
    own its own Protocol without importing across the ``runner/loop`` boundary
    (``bzh:domain-core``, ``bzh:dependency-inversion``: the domain declares the seam it
    needs). Implementations satisfy it structurally — no shared base class.
    """

    def is_alive(self, pid: int, process_start_time: str) -> bool: ...


class LocalLeaseService:
    """Derive every active lease's state at read time — the panel's list (issue #28).

    A status the store never stores, computed here from facts plus the injected clock and
    process probe. Holds only :class:`IReadRunnerStore` (``bzh:repository-split``) — this
    is a read path, so it is safe for a controller to hold this service directly
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
        would be speculative): ``latest_heartbeat``, :func:`last_activity` for the
        staleness read, and ``bindings_for_chunk`` for the environment join.

        The reported heartbeat and the staleness baseline are different questions — the
        former is what the *worker* last did, the latter is measured against the spawn too
        (issue #150) — but they share the one heartbeat read rather than issuing it twice.
        """
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

        Ordering is server-owned (one owner): every active lease first — unbounded, so a
        long-running agent can never be crowded out — then up to ``recent_limit`` closed
        leases, newest-closed first. Pinned by ``tests/test_runner_leases_domain.py::
        test_list_recent_active_lease_not_crowded_out_by_newer_closed_leases``.
        """
        return self.list_active() + self._list_closed()

    def _list_closed(self) -> list[LeaseActivity]:
        """The recent-closed half of :meth:`list_recent` — no probe, no heartbeat read.

        ``closed`` wins :func:`derive_lease_state`'s precedence unconditionally, so the
        process-liveness and staleness reads would be wasted I/O here — and the pid read
        would be actively misleading (a closed lease's pid may have been reused by an
        unrelated process). ``environment_id``/``workdir`` are ``None``: a closed lease's
        bindings are always released by the time closure is recorded (issue #29).
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
