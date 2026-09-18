"""The lease-liveness repository seam — heartbeat and spawn facts, REAP's staleness
baseline."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from blizzard.foundation.clock import IClock
from blizzard.runner.harness.identity import SessionReference

if TYPE_CHECKING:
    from blizzard.runner.domain.leases import LeaseRecord

__all__ = ["IReadLeaseLivenessRepository", "IWriteLeaseLivenessRepository", "LeaseLivenessService"]


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

        The fallback half of the staleness baseline (issue #150). A lease outlives its
        processes, so the newest ``lease_spawns`` row is when the running worker started."""
        ...

    def lease_generation(self, lease_id: str) -> int:
        """This lease's current spawn generation — the count of its ``lease_spawns`` rows
        (issue #58): 1 at the initial spawn, incrementing at each resume that calls
        ``record_spawn`` again under this lease. Usage's idempotency co-key
        (:meth:`IWriteLeaseLivenessRepository.record_usage`) and its kind discriminator —
        generation 1 is a ``spawn``, every later generation a ``resume``."""
        ...


class IWriteLeaseLivenessRepository(IReadLeaseLivenessRepository, Protocol):
    """Read-write liveness store — held only by the domain (the loop steps)."""

    def record_heartbeat(self, *, lease_id: str, beat_at: datetime) -> None:
        """Append a heartbeat for a lease — a worker tool call fired its hook."""
        ...

    def prune_heartbeats(self, *, now: datetime) -> int:
        """Compact heartbeats older than the store's own retention window (issue #520),
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
    ) -> None:
        """Fill a lease's spawn-return facts in one shot: pid, process start time, process
        group, session id — for identity known at spawn time (a fresh mint instead splits
        this across :meth:`record_provisional_spawn`/:meth:`record_identified_spawn`, D1/D2).
        ``pgid`` defaults to ``None`` when the launch's owned group is unknown. ``spawned_at``
        appends the lease's spawn generation, distinguishing this fact from a stale one (issue #13)."""
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
        """Phase one of a two-phase spawn (D1/D2): durable BEFORE identity is known — the
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
    ) -> None:
        """Phase two (D1/D2): fill the open provisional generation's authoritative session
        id — the newest ``lease_spawns`` row for this lease with no ``session_id`` yet —
        and the lease's own ``session_id``/``harness_id``. Also opens/carries-forward the
        lease's transcript segment, mirroring :meth:`record_spawn`'s own segment handling."""
        ...

    def record_identity_failed(self, lease_id: str, *, at: datetime) -> None:
        """A provisional generation's identity never arrived (D2): timeout, a malformed
        reply, or the process exiting first. Marks the newest still-open ``lease_spawns``
        row rather than leaving it ambiguously open forever; the lease's own ``session_id``
        stays ``None``, so REAP's ordinary "unspawned" recovery reaps it exactly as it would
        any lease that never got this far — this is a record of why, not a new state."""
        ...


class LeaseLivenessService:
    """Composition-root-wired: the liveness store and the clock (D4, blizzard#412)."""

    def __init__(self, store: IWriteLeaseLivenessRepository, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def record_heartbeat(self, lease: LeaseRecord) -> None:
        """Record a lease heartbeat, stamped with the injected clock.

        ``lease`` is already resolved by the caller (``bzh:domain-takes-objects``)."""
        self._store.record_heartbeat(lease_id=lease.lease_id, beat_at=self._clock.now())
