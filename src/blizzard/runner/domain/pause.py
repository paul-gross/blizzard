"""The hub-mirrored/local pause brake and daemon-liveness repository seam (blizzard#410)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.wire.facts import RUNNER_LOCALLY_PAUSED, RUNNER_LOCALLY_RESUMED

__all__ = ["IReadPauseRepository", "IWritePauseRepository", "PauseParkRecord", "PauseService"]


@dataclass(frozen=True)
class PauseParkRecord:
    """One open pause park (issue #46, blizzard#627) — the facts the park's own teardown
    reads back each tick until nothing of the lease's is alive."""

    lease_id: str
    chunk_id: str
    parked_at: datetime
    #: The elicitation record this park's interrupt signalled; unnamed-and-standing means a usage-limit judge park.
    interrupted_elicitation_id: int | None


class IReadPauseRepository(Protocol):
    """Read-only pause-brake and daemon-liveness queries (held by read-path edges)."""

    def hub_contact_at(self, runner_id: str) -> datetime | None:
        """When the runner last **successfully** reached the hub, or ``None`` if never (issue #51).

        :meth:`~IWritePauseRepository.set_hub_paused` is only called after a successful hub
        round trip (``runner/loop/steps.py``), so its ``updated_at`` **is** the last-successful-
        contact instant — no separate fact needed (``bzh:facts-not-status``)."""
        ...

    def hub_paused(self, runner_id: str) -> bool:
        """The last hub pause brake value mirrored locally — consulted before claiming new work.

        Defaults False when it has never been synced (a fresh runner claims freely until it
        first hears otherwise)."""
        ...

    def local_paused(self, runner_id: str) -> bool:
        """This runner's own brake, derived from the newest local pause fact (issue #43).

        Distinct from ``hub_paused``: it blocks every spawn site, not claims alone (issue
        #45). Defaults False when the operator has never set it."""
        ...

    def local_pause_reason(self, runner_id: str) -> str | None:
        """The newest local pause fact's own reason (blizzard#594) — ``None`` on a plain
        operator pause, or when the brake has never been set. Read independently of
        :meth:`local_paused` so a caller decides for itself whether to consult it."""
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

    def open_pause_parks(self) -> dict[str, PauseParkRecord]:
        """Every open pause park by lease id — :meth:`pause_parked_lease_ids`'s leases, each with
        its ``parked_at`` and the elicitation its interrupt signalled (blizzard#627). Hoisted once
        per tick (``bzh:bulk-reconstitution``) for the teardown ADVANCE completes over later
        ticks. A lease re-parked across a crash reads its newest park."""
        ...


class IWritePauseRepository(IReadPauseRepository, Protocol):
    """Read-write pause-brake and daemon-liveness store — held only by the domain."""

    def record_daemon_liveness(self, *, runner_id: str, alive_at: datetime) -> None:
        """Stamp the runner as alive at ``alive_at`` — the tick's liveness beat (issue #13).

        Upserted, one row per runner: only the newest instant matters, and it is the crash-time
        reference startup recovery reads back via :meth:`last_daemon_liveness`."""
        ...

    def set_hub_paused(self, runner_id: str, *, paused: bool, at: datetime) -> None:
        """Mirror the hub's pause brake locally (upsert) — read back before claiming new work."""
        ...

    def record_local_pause(
        self,
        runner_id: str,
        *,
        paused: bool,
        at: datetime,
        by: str,
        report_kind: str,
        report_payload: str,
        reason: str | None = None,
    ) -> int:
        """Append a local pause/start fact **and** its hub-bound report, atomically
        (issue #43), and return the buffered report's seq. Appends rather than upserts:
        a locally-minted fact, not a mirror; taking the buffer entry here makes the
        brake and its report crash-atomic (``tests/test_ingest_and_pause_verbs.py``).
        ``reason`` is stored alongside the fact so :meth:`~IReadPauseRepository.local_pause_reason`
        can read it back locally, not only through the hub-bound report (blizzard#594)."""
        ...

    def record_pause_park(
        self, *, lease_id: str, chunk_id: str, parked_at: datetime, interrupted_elicitation_id: int | None = None
    ) -> None:
        """Park a lease on an operator pause — dormant, its env bindings held (issue #46).
        ``interrupted_elicitation_id`` names the in-flight elicitation the park's own
        interrupt signalled, in the same insert (``bzh:facts-not-status``); ``None`` when it
        signalled none."""
        ...

    def record_pause_park_resume(self, *, lease_id: str, resumed_at: datetime) -> None:
        """End a lease's pause-park — the operator resumed it (issue #46)."""
        ...


class PauseService:
    """Composition-root-wired: the pause store, the clock, and the optional event
    publisher (D4, blizzard#412)."""

    def __init__(
        self, store: IWritePauseRepository, clock: IClock, *, events: IRunnerEventPublisher | None = None
    ) -> None:
        self._store = store
        self._clock = clock
        self._events = events

    def set_local_pause(self, runner_id: str, *, paused: bool, by: str) -> None:
        """Set this runner's own pause brake and its upward report, atomically (issue #43).

        The brake and its hub-bound report are one write: mirroring runs hub→runner only,
        so a brake never reported up would never be repaired
        (``tests/test_ingest_and_pause_verbs.py``)."""
        now = self._clock.now()
        report_kind = RUNNER_LOCALLY_PAUSED if paused else RUNNER_LOCALLY_RESUMED
        seq = self._store.record_local_pause(
            runner_id,
            paused=paused,
            at=now,
            by=by,
            report_kind=report_kind,
            report_payload=json.dumps({"runner_id": runner_id, "by": by, "at": iso_utc(now)}),
        )
        if self._events is not None:
            self._events.publish_fact_changed(seq=seq, kind=report_kind, chunk_id=None, lease_id=None)

    def engage(self, runner_id: str, *, by: str, reason: str) -> None:
        """Engage the local brake with a durable reason (blizzard#594) — once: already
        engaged for any cause (an operator pause, the spend ceiling, a prior usage limit)
        leaves the standing fact and its reason untouched, exactly as :meth:`set_local_pause`
        is engage-idempotent from the operator's own side. The operator's clear
        (:meth:`set_local_pause` with ``paused=False``) is the only lift."""
        if self._store.local_paused(runner_id):
            return
        now = self._clock.now()
        seq = self._store.record_local_pause(
            runner_id,
            paused=True,
            at=now,
            by=by,
            report_kind=RUNNER_LOCALLY_PAUSED,
            report_payload=json.dumps({"runner_id": runner_id, "by": by, "at": iso_utc(now), "reason": reason}),
            reason=reason,
        )
        if self._events is not None:
            self._events.publish_fact_changed(seq=seq, kind=RUNNER_LOCALLY_PAUSED, chunk_id=None, lease_id=None)
