"""The lease-liveness repository seam — heartbeat and spawn facts, REAP's staleness
baseline."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import dto
from blizzard.runner.harness.identity import SessionReference

if TYPE_CHECKING:
    from blizzard.runner.leases import Lease

__all__ = [
    "IReadLeaseLivenessRepository",
    "IWriteLeaseLivenessRepository",
    "LeaseLivenessFacts",
    "LeaseLivenessService",
]


@dto
@dataclass(frozen=True)
class LeaseLivenessFacts:
    """One lease's :meth:`~IReadLeaseLivenessRepository.latest_heartbeat` and
    :meth:`~IReadLeaseLivenessRepository.latest_spawn`, read together —
    :meth:`~IReadLeaseLivenessRepository.liveness_facts`'s own per-lease value."""

    latest_heartbeat: datetime | None
    latest_spawn: datetime | None


class IReadLeaseLivenessRepository(Protocol):
    """Read-only heartbeat and spawn queries backing the lease-liveness staleness baseline
    (held by read-path edges)."""

    def latest_heartbeat(self, lease_id: str) -> datetime | None:
        """The lease's most recent heartbeat stamp, or ``None`` if it never beat.

        The primary signal in the staleness baseline; on ``None`` the caller falls back to
        :meth:`latest_spawn`."""
        ...

    def latest_spawn(self, lease_id: str) -> datetime | None:
        """When this lease's newest process was spawned, or ``None`` if it never was.

        The fallback half of the staleness baseline. A lease outlives its
        processes, so the newest ``lease_spawns`` row is when the running worker started."""
        ...

    def liveness_facts(self, lease_ids: Sequence[str]) -> dict[str, LeaseLivenessFacts]:
        """:meth:`latest_heartbeat` and :meth:`latest_spawn`, for every id in ``lease_ids``,
        in two grouped reads rather than one round trip per lease (`bzh:bulk-reconstitution`).
        A lease with neither fact is absent from the result, exactly as the singular getters
        would both answer ``None`` for it."""
        ...

    def lease_generations(self, lease_ids: Sequence[str]) -> dict[str, int]:
        """:meth:`lease_generation` for every id in ``lease_ids``, in one grouped read.
        An id with no ``lease_spawns`` row is absent — the caller reads that the same as
        the singular's own ``0``."""
        ...

    def lease_generation(self, lease_id: str) -> int:
        """This lease's current spawn generation — the count of its ``lease_spawns`` rows:
        1 at the initial spawn, incrementing at each resume that calls
        ``record_spawn`` again under this lease. Usage's idempotency co-key
        (:meth:`IWriteLeaseLivenessRepository.record_usage`) and its kind discriminator —
        generation 1 is a ``spawn``, every later generation a ``resume``."""
        ...

    def latest_spawn_harness_version(self, lease_id: str) -> str | None:
        """The current generation's own recorded ``harness_version`` — the
        newest ``lease_spawns`` row's own stamp, read as recorded and never re-resolved.
        ``None`` when the lease never spawned, or its newest generation recorded no
        version — never a guess at what version is running now."""
        ...


class IWriteLeaseLivenessRepository(IReadLeaseLivenessRepository, Protocol):
    """Read-write liveness store — held only by the domain (the loop steps)."""

    def record_heartbeat(self, *, lease_id: str, beat_at: datetime) -> None:
        """Append a heartbeat for a lease — a worker tool call fired its hook."""
        ...

    def prune_heartbeats(self, *, now: datetime) -> int:
        """Compact heartbeats older than the store's own retention window,
        keeping each lease's newest beat regardless of age — ``max(beat_at)`` per lease is
        unchanged, so :meth:`~IReadLeaseLivenessRepository.latest_heartbeat` answers
        identically before and after. Returns the number of rows pruned."""
        ...

    def record_spawn(
        self,
        lease_id: str,
        *,
        pid: int,
        process_start_time: str,
        spawned_at: datetime,
        session: SessionReference,
        harness_version: str | None = None,
        pgid: int | None = None,
        spawn_cwd: str | None = None,
    ) -> None:
        """Fill a lease's spawn-return facts in one shot: pid, process start time, process
        group, session id — for identity known at spawn time (a fresh mint instead splits
        this across :meth:`record_provisional_spawn`/:meth:`record_identified_spawn`).
        ``pgid`` defaults to ``None`` when the launch's owned group is unknown. ``spawned_at``
        appends the lease's spawn generation, distinguishing this fact from a stale one.
        ``spawn_cwd`` is the worker's working directory, frozen onto the opened transcript segment."""
        ...

    def record_provisional_spawn(
        self,
        lease_id: str,
        *,
        pid: int,
        process_start_time: str,
        pgid: int | None,
        spawned_at: datetime,
        harness_id: str,
    ) -> None:
        """Phase one of a two-phase spawn: durable BEFORE identity is known — the
        launched process's pid, start time, and owned group, plus a new ``lease_spawns``
        generation row with no session id yet. The lease's own AUTHORITATIVE
        ``session_id``/``harness_id`` are untouched here; :meth:`record_identified_spawn`
        sets them once identity lands."""
        ...

    def record_identified_spawn(
        self,
        lease_id: str,
        *,
        session: SessionReference,
        identified_at: datetime,
        harness_version: str | None = None,
        spawn_cwd: str | None = None,
    ) -> None:
        """Phase two: fill the open provisional generation's authoritative session
        id — the newest ``lease_spawns`` row for this lease with no ``session_id`` yet —
        and the lease's own ``session_id``/``harness_id``. Also opens/carries-forward the
        lease's transcript segment, mirroring :meth:`record_spawn`'s own segment handling, ``spawn_cwd`` included."""
        ...

    def record_identity_failed(self, lease_id: str, *, at: datetime) -> None:
        """A provisional generation's identity never arrived: timeout, a malformed
        reply, or the process exiting first. Marks the newest still-open ``lease_spawns``
        row rather than leaving it ambiguously open forever; the lease's own ``session_id``
        stays ``None``, so REAP's ordinary "unspawned" recovery reaps it exactly as it would
        any lease that never got this far — this is a record of why, not a new state."""
        ...


class LeaseLivenessService:
    """Composition-root-wired: the liveness store and the clock."""

    def __init__(self, store: IWriteLeaseLivenessRepository, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def record_heartbeat(self, lease: Lease) -> None:
        """Record a lease heartbeat, stamped with the injected clock.

        ``lease`` is already resolved by the caller (``bzh:domain-takes-objects``)."""
        self._store.record_heartbeat(lease_id=lease.lease_id, beat_at=self._clock.now())
