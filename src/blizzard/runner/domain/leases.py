"""Lease staleness and derived lease state (``bzh:domain-core``).

The one owner of "when does a live worker read as stalled" — two copies of that
predicate would let one reader say ``running`` while another reaps the same lease. Also
holds the read model, which derives state from facts at read time
(``bzh:facts-not-status``). Stdlib and seam Protocols only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.store.utc import as_utc
from blizzard.runner.environments.repository import EnvBindingRecord
from blizzard.runner.harness.fingerprint import PreambleFingerprint

if TYPE_CHECKING:
    # Deferred: ``runner/stores.py`` composes this module's own Protocol (blizzard#410).
    from blizzard.runner.stores import IReadRunnerStore

__all__ = [
    "HEARTBEAT_STALENESS_THRESHOLD",
    "RECENT_LEASE_LIMIT",
    "ClosedLeaseRecord",
    "IProcessProbe",
    "IReadLeaseRepository",
    "IWriteLeaseRepository",
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


class IReadLeaseRepository(Protocol):
    """Read-only lease queries — mint, spawn, heartbeat, closure, session, epoch, and
    preamble facts (held by read-path edges)."""

    def list_active_leases(self) -> list[LeaseRecord]:
        """Leases with no closure fact — the attempts currently in flight."""
        ...

    def active_lease_for_chunk(self, chunk_id: str) -> LeaseRecord | None:
        """The chunk's single active lease, if any (P6: at most one — MAX_AGENTS math)."""
        ...

    def active_lease(self, lease_id: str) -> LeaseRecord | None:
        """The lease by id iff it is still active (no closure fact), else ``None``.

        The flusher's ack-idempotency check: an already-closed lease means the completion
        applied on an earlier flush whose ack was lost.
        """
        ...

    def latest_lease_for_chunk(self, chunk_id: str) -> LeaseRecord | None:
        """The chunk's most-recently-minted lease, active or closed (issue #52).

        Unlike :meth:`active_lease_for_chunk`, spans closed leases too: a takeover can be
        requested with no active lease left, and the closed one still carries the session
        id it resumes."""
        ...

    def latest_session_id(self, chunk_id: str, node_name: str | None) -> str | None:
        """The chunk's most-recent session-bearing lease's ``session_id``, or ``None``.

        The newest lease for this chunk whose ``session_id`` is non-null, optionally
        filtered to ``node_name`` (issue #115). ``None`` is the fresh-fallback signal."""
        ...

    def pool_head(self, chunk_id: str, session_name: str) -> PoolHead | None:
        """The named session pool's current head for this chunk, or ``None`` (issue #144).

        The newest session-bearing lease whose ``lease_context.session_name`` matches;
        derived, never a column. **Runner-local**: a chunk reclaimed elsewhere mints fresh.
        """
        ...

    def session_invocation_count(self, session_id: str) -> int:
        """How many harness invocations this session has recorded (issue #144).

        The signal behind a declared ``rotate.max_invocations`` — ``usage_facts`` rows
        across every lease that ran ``session_id``. **Harness invocations, not
        node-steps.** Zero is a real answer here, not an unknown."""
        ...

    def lease_for_session(self, session_id: str) -> LeaseRecord | None:
        """The newest lease that ran ``session_id``, or ``None`` (issue #144).

        Keyed on the *session*, which outlives the lease that minted it: several leases
        share one session id and the newest describes the running configuration."""
        ...

    def lease(self, lease_id: str) -> LeaseRecord | None:
        """The lease by id, regardless of closure — the transcript read (issue #29).

        Distinct from :meth:`active_lease`: a transcript outlives its lease.
        """
        ...

    def list_closed_leases(self, limit: int) -> list[ClosedLeaseRecord]:
        """The most recently closed leases, newest first — the panel's recent-history
        read (issue #29).

        ``limit`` bounds rows returned, never how long a closure fact lives on disk.
        """
        ...

    def latest_heartbeat(self, lease_id: str) -> datetime | None:
        """The lease's most recent heartbeat stamp, or ``None`` if it never beat.

        REAP's stall signal; on ``None`` the caller falls back to :meth:`latest_spawn`."""
        ...

    def latest_spawn(self, lease_id: str) -> datetime | None:
        """When this lease's newest process was spawned, or ``None`` if it never was.

        The second half of REAP's staleness baseline (issue #150). A lease outlives its
        processes, so the newest ``lease_spawns`` row is when the running worker started."""
        ...

    def attempt_count(self, chunk_id: str, node_id: str) -> int:
        """How many leases have been minted for this chunk at this node (retry budget).

        Excludes an attempt an operator's restart preempted (issue #370) — that attempt was
        superseded rather than spent, so it does not carry the node toward exhaustion."""
        ...

    def latest_epoch(self, chunk_id: str) -> int:
        """The highest lease epoch minted for this chunk, or 0 — the fence source."""
        ...

    def lease_generation(self, lease_id: str) -> int:
        """This lease's current spawn generation — the count of its ``lease_spawns`` rows
        (issue #58): 1 at the initial spawn, incrementing at each resume that calls
        ``record_spawn`` again under this lease. Usage's idempotency co-key
        (:meth:`IWriteLeaseRepository.record_usage`) and its kind discriminator — generation 1
        is a ``spawn``, every later generation a ``resume``."""
        ...

    def lease_ids_for_chunk(self, chunk_id: str) -> list[str]:
        """Every lease id ever minted for this chunk, active or closed (issue #58).

        A chunk's tenure can span several node-steps and retries, each its own lease —
        this is the release-time read that finds every one of them, not just the
        currently-active lease."""
        ...

    def resume_intent_lease_ids(self) -> set[str]:
        """Leases carrying an **open** restart resume-intent.

        A ``resume_intents`` mark with no ``resume_clears`` for the same lease at or
        after it (#12, #13). Empty on any normal tick; non-empty only on the first tick
        after a restart."""
        ...

    def session_ended_lease_ids(self) -> set[str]:
        """Leases whose **current spawn** recorded a session-end — it declared done.

        A dead pid *with* a session-end is a done declaration, not a crash to re-attach.
        Scoped to the lease's newest ``lease_spawns`` fact, because a lease outlives its
        sessions and an unscoped read would suppress every later crash's resume."""
        ...

    def session_preamble_fingerprint(self, session_id: str) -> PreambleFingerprint | None:
        """The standing preamble prose this session was last sent, or ``None`` (issue #149).

        The newest ``session_preamble_facts`` row for the session. ``None`` renders the
        full preamble — the safe direction, since an over-eager match would cost the
        worker its updated instructions."""
        ...


class IWriteLeaseRepository(IReadLeaseRepository, Protocol):
    """Read-write lease store — held only by the domain (the loop steps)."""

    def record_lease(self, lease: NewLease) -> None:
        """Persist a minted lease and its node context, atomically."""
        ...

    def record_spawn(
        self, lease_id: str, *, pid: int, process_start_time: str, session_id: str, spawned_at: datetime
    ) -> None:
        """Fill a lease's spawn-return facts: pid, process start time, session id.

        ``spawned_at`` additionally appends the lease's spawn generation, so a fact recorded
        by an earlier session of the same lease can be told from one recorded by the process
        running now (issue #13)."""
        ...

    def record_heartbeat(self, *, lease_id: str, beat_at: datetime) -> None:
        """Append a heartbeat for a lease — a worker tool call fired its hook."""
        ...

    def record_closure(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        reason: str,
        closed_at: datetime,
        event_kind: str | None = None,
        event_payload: str | None = None,
    ) -> int | None:
        """Close a lease — a clean transition or a failure/escalation.

        When ``event_kind``/``event_payload`` are given (issue #125), the event is
        enqueued to the outbound buffer **in the same transaction** as the closure —
        the two land together or not at all; return its seq, ``None`` when no event."""
        ...

    def record_resume_intent(self, *, lease_id: str, marked_at: datetime) -> None:
        """Mark a lease for same-lease restart-resume at graceful shutdown."""
        ...

    def record_resume_clear(self, *, lease_id: str, cleared_at: datetime) -> None:
        """Clear a lease's resume-intent — the RESUME step resumed or abandoned it."""
        ...

    def record_session_end(self, *, lease_id: str, ended_at: datetime) -> None:
        """Record a worker's session-end — the ``SessionEnd`` hook fired on exit."""
        ...

    def record_session_preamble(self, session_id: str, *, fingerprint: PreambleFingerprint, at: datetime) -> None:
        """Record what standing preamble prose this session was just sent (issue #149).

        Append-only; the newest row is what the fingerprint read returns. The fact is
        *"this prose was sent to this session"*, not *"a spawn happened"*, and is written
        after the spawn so a durable fingerprint implies the prose reached the process."""
        ...


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
        cls, store: IReadRunnerStore, lease: LeaseRecord, *, heartbeat: datetime | None | _Unread = _UNREAD
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
            liveness = Liveness.of(self._store, lease, heartbeat=last_heartbeat)
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
            for record in self._store.list_closed_leases(self._recent_limit)
        ]

    def _is_alive(self, lease: LeaseRecord) -> bool:
        if lease.pid is None:
            return False  # spawning — `LeaseActivity.state` short-circuits before this matters
        return self._process.is_alive(lease.pid, lease.process_start_time or "")

    def _first_binding(self, chunk_id: str) -> EnvBindingRecord | None:
        bindings = self._store.bindings_for_chunk(chunk_id)
        return bindings[0] if bindings else None
