"""The lease-liveness repository seam — heartbeat and spawn facts, REAP's staleness
baseline."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock

__all__ = ["IReadLeaseLivenessRepository", "IWriteLeaseLivenessRepository", "LeaseLivenessService"]


class IReadLeaseLivenessRepository(Protocol):
    """Read-only heartbeat and spawn queries — REAP's staleness baseline (held by
    read-path edges)."""

    def latest_heartbeat(self, lease_id: str) -> datetime | None:
        """The lease's most recent heartbeat stamp, or ``None`` if it never beat.

        REAP's stall signal; on ``None`` the caller falls back to :meth:`latest_spawn`."""
        ...

    def latest_spawn(self, lease_id: str) -> datetime | None:
        """When this lease's newest process was spawned, or ``None`` if it never was.

        The second half of REAP's staleness baseline (issue #150). A lease outlives its
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

    def record_spawn(
        self, lease_id: str, *, pid: int, process_start_time: str, session_id: str, spawned_at: datetime
    ) -> None:
        """Fill a lease's spawn-return facts: pid, process start time, session id.

        ``spawned_at`` additionally appends the lease's spawn generation, so a fact recorded
        by an earlier session of the same lease can be told from one recorded by the process
        running now (issue #13)."""
        ...


class LeaseLivenessService:
    """Composition-root-wired: the liveness store and the clock (D4, blizzard#412)."""

    def __init__(self, store: IWriteLeaseLivenessRepository, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def record_heartbeat(self, lease_id: str) -> None:
        """Record a lease heartbeat, stamped with the injected clock."""
        self._store.record_heartbeat(lease_id=lease_id, beat_at=self._clock.now())
