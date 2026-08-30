"""The hub-mirrored/local pause brake and daemon-liveness repository seam (blizzard#410)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

__all__ = ["IReadPauseRepository", "IWritePauseRepository"]


class IReadPauseRepository(Protocol):
    """Read-only pause-brake and daemon-liveness queries (held by read-path edges)."""

    def hub_contact_at(self, runner_id: str) -> datetime | None:
        """When PULL last **successfully** reached the hub, or ``None`` if never (issue #51).

        :meth:`~IWritePauseRepository.set_hub_paused` is only called after a successful hub
        round trip (``runner/loop/steps.py``), so its ``updated_at`` **is** the last-successful-
        contact instant — no separate fact needed (``bzh:facts-not-status``)."""
        ...

    def hub_paused(self, runner_id: str) -> bool:
        """The last hub pause brake PULL mirrored locally — FILL adheres.

        Defaults False when PULL has never synced (a fresh runner claims freely until it
        first hears otherwise)."""
        ...

    def local_paused(self, runner_id: str) -> bool:
        """This runner's own brake, derived from the newest local pause fact (issue #43).

        Distinct from ``hub_paused``: it blocks every spawn site, not claims alone (issue
        #45). Defaults False when the operator has never set it."""
        ...

    def last_daemon_liveness(self) -> datetime | None:
        """When the runner was last known alive, or ``None`` if it never ticked (issue #13).

        The crash-time reference startup recovery classifies staleness against, stamped
        each tick, so the newest value is when the daemon died to within one tick."""
        ...

    def pause_parked_lease_ids(self) -> set[str]:
        """Leases dormant on an operator pause — a pause-park fact with no later
        pause-resume at or after it (issue #46).

        The pause-park half of
        :meth:`~blizzard.runner.domain.asks.IReadAskRepository.parked_lease_ids`'s union."""
        ...


class IWritePauseRepository(IReadPauseRepository, Protocol):
    """Read-write pause-brake and daemon-liveness store — held only by the domain."""

    def record_daemon_liveness(self, *, runner_id: str, alive_at: datetime) -> None:
        """Stamp the runner as alive at ``alive_at`` — the tick's liveness beat (issue #13).

        Upserted, one row per runner: only the newest instant matters, and it is the crash-time
        reference startup recovery reads back via :meth:`last_daemon_liveness`."""
        ...

    def set_hub_paused(self, runner_id: str, *, paused: bool, at: datetime) -> None:
        """Mirror the hub's pause brake locally (upsert) — read back by FILL."""
        ...

    def record_local_pause(
        self, runner_id: str, *, paused: bool, at: datetime, by: str, report_kind: str, report_payload: str
    ) -> int:
        """Append a local pause/start fact **and** its hub-bound report, atomically
        (issue #43), and return the buffered report's seq. Appends rather than upserts:
        a locally-minted fact, not a mirror; taking the buffer entry here makes the
        brake and its report crash-atomic (``tests/test_ingest_and_pause_verbs.py``)."""
        ...

    def record_pause_park(self, *, lease_id: str, chunk_id: str, parked_at: datetime) -> None:
        """Park a lease on an operator pause — dormant, its env bindings held (issue #46)."""
        ...

    def record_pause_park_resume(self, *, lease_id: str, resumed_at: datetime) -> None:
        """End a lease's pause-park — the operator resumed it (issue #46)."""
        ...
