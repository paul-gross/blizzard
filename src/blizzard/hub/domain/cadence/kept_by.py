"""Which chores keep a routine (``bzh:routine-kept-by``).

A pure function over a loaded routine and loaded cadences; it reads no clock and no store."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.cadence.model import Cadence
from blizzard.hub.domain.garden.routines import Routine


@domain_model
@dataclass(frozen=True)
class KeptBy:
    """One chore keeping a routine, with the next moment of its cadence's schedule (``None`` without one)."""

    cadence_slug: str
    cadence_name: str
    chore_key: str
    next_moment: datetime | None


def kept_by(routine: Routine, cadences: Sequence[Cadence], now: datetime) -> tuple[KeptBy, ...]:
    """A row per chore naming ``routine`` on a non-retired cadence of its project, in the given cadence order and
    then chore order. A retired routine is still kept, and attention does not unkeep it."""
    rows: list[KeptBy] = []
    for cadence in cadences:
        if cadence.retired or cadence.project_id != routine.project_id:
            continue
        schedule = cadence.rhythm.schedule
        moment = None if schedule is None else schedule.first_after(now)
        rows.extend(
            KeptBy(cadence.slug, cadence.name, chore.key, moment)
            for chore in cadence.chores
            if chore.spec.routine_slug == routine.slug
        )
    return tuple(rows)
