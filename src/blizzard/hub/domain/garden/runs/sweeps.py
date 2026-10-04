"""A routine's per-scope last-swept table and its windowed measurement series — a read
over `finding_sets`, each row joined to its own artifact's `produced_at`. Last-swept is
unwindowed: a scope swept months ago must never read as never. The measurement
series is cut to `[since, until)`, the same window `findings/trend.py`'s own read reports
over; the cut is done in Python, not SQL (`bzh:sql-portable`), the same split
`findings/trend.py` makes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model, dto
from blizzard.hub.domain.garden.routines import IReadRoutineScopeRepository, Routine
from blizzard.hub.domain.garden.runs.window import require_until_after_since
from blizzard.hub.domain.garden.scopes import IReadScopeRepository


@dto
@dataclass(frozen=True)
class SweepFact:
    """One `finding_sets` row for a routine, joined to its own artifact's `produced_at`
    — the shape `sweeps_for_routine` returns, unwindowed."""

    finding_set_id: str
    scope_slug: str
    produced_at: datetime
    revisions: dict[str, str]
    measurement: str | None


@dto
@dataclass(frozen=True)
class ScopeSweep:
    """One row of the last-swept table — `finding_set_id`/`produced_at` `None`
    marks a scope this routine has never swept."""

    scope_slug: str
    finding_set_id: str | None
    produced_at: datetime | None
    revisions: dict[str, str]


@dto
@dataclass(frozen=True)
class MeasurementReading:
    """One recorded measurement inside the window — opaque text, never parsed."""

    scope_slug: str
    produced_at: datetime
    measurement: str


@dto
@dataclass(frozen=True)
class GardenSweeps:
    routine_name: str
    since: datetime
    until: datetime
    last_swept: list[ScopeSweep]
    measurements: list[MeasurementReading]


class IReadGardenSweepsRepository(Protocol):
    def sweeps_for_routine(self, routine_name: str) -> list[SweepFact]:
        """Every `finding_sets` row for `routine_name`, unwindowed, each joined to
        its own artifact's `produced_at` — `finding_sets` carries no timestamp of its
        own."""
        ...


def compute_sweeps(
    facts: list[SweepFact],
    *,
    routine_name: str,
    scope_slugs: list[str],
    since: datetime,
    until: datetime,
) -> GardenSweeps:
    """Fold `facts` (already unwindowed) into the last-swept table over `scope_slugs` —
    the routine's declared set, retired scopes already filtered out by the caller
    — and the windowed measurement series. One pass over `facts`:
    newest-per-scope by `produced_at`, ties broken by `finding_set_id`
    (ULID-monotonic, `findings/trend.py`'s own tie convention)."""
    newest: dict[str, SweepFact] = {}
    for fact in facts:
        current = newest.get(fact.scope_slug)
        if current is None or (fact.produced_at, fact.finding_set_id) > (
            current.produced_at,
            current.finding_set_id,
        ):
            newest[fact.scope_slug] = fact
    covered = set(scope_slugs) | set(newest)
    last_swept = []
    for slug in sorted(covered):
        fact = newest.get(slug)
        last_swept.append(
            ScopeSweep(
                scope_slug=slug,
                finding_set_id=fact.finding_set_id if fact else None,
                produced_at=fact.produced_at if fact else None,
                revisions=dict(fact.revisions) if fact else {},
            )
        )
    measurements = sorted(
        (
            MeasurementReading(scope_slug=fact.scope_slug, produced_at=fact.produced_at, measurement=fact.measurement)
            for fact in facts
            if fact.measurement is not None and since <= fact.produced_at < until
        ),
        key=lambda m: m.produced_at,
    )
    return GardenSweeps(
        routine_name=routine_name, since=since, until=until, last_swept=last_swept, measurements=measurements
    )


@domain_model
@dataclass(frozen=True)
class SweepWindow:
    """The ``[since, until)`` a measurement series reads; an empty or inverted span
    refuses."""

    since: datetime
    until: datetime

    @classmethod
    def of(cls, since: datetime, until: datetime) -> SweepWindow:
        require_until_after_since(since, until)
        return cls(since=since, until=until)


def sweep_coverage(declared: set[str], retired: set[str], facts: list[SweepFact]) -> tuple[list[str], list[SweepFact]]:
    """The scopes a routine's last-swept table covers and the facts it folds: every
    declared scope not retired, plus a retired declared one only where a fact already
    records it swept. A scope unlinked since being swept never resurfaces through its
    own fact."""
    live_declared = sorted(slug for slug in declared if slug not in retired)
    return live_declared, [fact for fact in facts if fact.scope_slug in declared]


class GardenSweepsService:
    """Reads a routine's last-swept table and measurement series, delegating the fold
    to `compute_sweeps`. Takes the resolved `Routine` (`bzh:domain-takes-objects`) —
    existence is resolved at the edge, before this is ever invoked."""

    def __init__(
        self,
        *,
        repo: IReadGardenSweepsRepository,
        scopes: IReadScopeRepository,
        routine_scopes: IReadRoutineScopeRepository,
    ) -> None:
        self._repo = repo
        self._scopes = scopes
        self._routine_scopes = routine_scopes

    def sweeps(self, routine: Routine, *, since: datetime, until: datetime) -> GardenSweeps:
        live_declared, facts = sweep_coverage(
            set(self._routine_scopes.list_scopes(routine.routine_id)),
            self._scopes.retired_slugs(),
            self._repo.sweeps_for_routine(routine.name),
        )
        return compute_sweeps(facts, routine_name=routine.name, scope_slugs=live_declared, since=since, until=until)
