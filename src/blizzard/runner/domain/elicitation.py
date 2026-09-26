"""An in-flight judgement elicitation — the detached process a launch starts and a
later reconciliation pass collects, keyed ``(lease_id, epoch)`` (blizzard#443)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

__all__ = ["ElicitationRecord", "IReadElicitationRepository", "IWriteElicitationRepository"]


@dataclass(frozen=True)
class ElicitationRecord:
    """A launched elicitation not yet collected. ``pid``/``process_start_time`` are unset
    only in the un-armable gap between the durable record and the process actually
    starting (``advance.after-elicit-record.before-launch``)."""

    id: int  # the store's own row id, stable for the record's lifetime
    lease_id: str
    epoch: int
    pid: int | None
    process_start_time: str | None
    pgid: int | None  # this launch's owned process group (D3); unset with `pid` alike
    output_path: str
    first_launched_at: datetime
    relaunch_count: int


class IReadElicitationRepository(Protocol):
    """Read-only in-flight-elicitation queries (held by read-path edges)."""

    def in_flight_elicitation(self, lease_id: str, epoch: int) -> ElicitationRecord | None:
        """This lease's in-flight elicitation for ``epoch``, or ``None`` once collected,
        cleared on lease closure, or never launched."""
        ...

    def in_flight_elicitations(self, pairs: Sequence[tuple[str, int]]) -> dict[tuple[str, int], ElicitationRecord]:
        """:meth:`in_flight_elicitation` for every ``(lease_id, epoch)`` pair in ``pairs``,
        keyed by that same pair (`bzh:bulk-reconstitution`). A pair with no record is
        absent, exactly as the singular getter answers ``None`` for it."""
        ...

    def in_flight_elicitation_lease_ids(self) -> set[str]:
        """Every lease id with an in-flight elicitation record, regardless of epoch (D6): no
        path may re-mint or resume a lease while its elicitation is in flight, matching the
        already-established ``parked_lease_ids``/``pending_submission_lease_ids`` shape."""
        ...

    def in_flight_elicitations_by_lease(self) -> dict[str, ElicitationRecord]:
        """Every in-flight elicitation record, by lease id (regardless of epoch, matching
        :meth:`in_flight_elicitation_lease_ids`'s own shape). Hoisted once per tick
        (``bzh:bulk-reconstitution``) beside ``open_pause_parks`` for the pause park's
        teardown over later ticks, rather than one singular read per park per tick."""
        ...


class IWriteElicitationRepository(IReadElicitationRepository, Protocol):
    """Read-write in-flight-elicitation store — held only by the domain."""

    def record_elicitation_launch(self, lease_id: str, epoch: int, *, output_path: str, at: datetime) -> None:
        """Durably record a fresh launch BEFORE the process starts (D1) —
        ``pid``/``process_start_time`` land via :meth:`record_elicitation_started` once
        ``Popen`` returns."""
        ...

    def record_elicitation_started(
        self, lease_id: str, epoch: int, *, pid: int, process_start_time: str, pgid: int | None = None
    ) -> None:
        """Fill in the launched process's pid, start time, and owned group (D3) on
        ``Popen`` return — the same group-ownership fact a fresh spawn or resume records,
        so a lease closing mid-elicitation can group-kill it rather than a bare pid kill."""
        ...

    def record_elicitation_relaunch(self, lease_id: str, epoch: int, *, output_path: str) -> None:
        """A lost answer's relaunch (D5): a fresh ``output_path`` and pid slot, ``relaunch_count``
        incremented, ``first_launched_at`` left untouched — staleness is measured from the
        first launch and a relaunch never resets it."""
        ...

    def clear_elicitation(self, lease_id: str, epoch: int) -> None:
        """Retire the record once its verdict is collected, or once its lease closes out
        from under it (D7)."""
        ...
