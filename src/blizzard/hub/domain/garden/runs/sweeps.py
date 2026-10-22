"""A routine's per-scope last-swept table and its windowed measurement series — a read
over `finding_sets`, each row joined to its own artifact's `produced_at`. Last-swept is
unwindowed: a scope swept months ago must never read as never. The measurement
series is cut to `[since, until)` by the store, in SQL, like the garden trend window."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.garden.routines import IReadRoutineScopeRepository, Routine
from blizzard.hub.domain.garden.runs.window import require_until_after_since
from blizzard.hub.domain.garden.scopes import IReadScopeRepository


@domain_model
@dataclass(frozen=True)
class SweepFact:
    """One `finding_sets` row for a routine, joined to its own artifact's `produced_at`
    — the shape `last_swept_by_scope` returns, unwindowed."""

    finding_set_id: str
    scope_slug: str
    produced_at: datetime
    revisions: dict[str, str]


@domain_model
@dataclass(frozen=True)
class ScopeSweep:
    """One row of the last-swept table — `finding_set_id`/`produced_at` `None`
    marks a scope this routine has never swept."""

    scope_slug: str
    finding_set_id: str | None
    produced_at: datetime | None
    revisions: dict[str, str]


@domain_model
@dataclass(frozen=True)
class MeasurementReading:
    """One recorded measurement inside the window — opaque text, never parsed."""

    scope_slug: str
    produced_at: datetime
    measurement: str


@domain_model
@dataclass(frozen=True)
class GardenSweeps:
    routine_name: str
    since: datetime
    until: datetime
    last_swept: list[ScopeSweep]
    measurements: list[MeasurementReading]


class IReadGardenSweepsRepository(Protocol):
    def last_swept_by_scope(self, project_id: str, routine_name: str) -> list[SweepFact]:
        """The newest `finding_sets` row of `routine_name` per scope, unwindowed — newest by
        `(produced_at, finding_set_id)` (`IReadFindingSetRepository.newest_for_routine_scope`), each
        joined to its own artifact's `produced_at`, since `finding_sets` carries no timestamp of its
        own."""
        ...

    def measurements_in_window(
        self, project_id: str, routine_name: str, *, since: datetime, until: datetime
    ) -> list[MeasurementReading]:
        """Every recorded measurement of `routine_name` with `since <= produced_at < until`,
        ordered by `(produced_at, finding_set_id)`."""
        ...


def compute_sweeps(
    newest: list[SweepFact],
    measurements: list[MeasurementReading],
    *,
    routine_name: str,
    scope_slugs: list[str],
    since: datetime,
    until: datetime,
) -> GardenSweeps:
    """Assemble the last-swept table over `scope_slugs` — the routine's declared set, retired
    scopes already filtered out by the caller — from `newest` (the newest set per scope, already
    picked by the store), beside the windowed `measurements`."""
    by_scope = {fact.scope_slug: fact for fact in newest}
    last_swept = []
    for slug in sorted(set(scope_slugs) | set(by_scope)):
        fact = by_scope.get(slug)
        last_swept.append(
            ScopeSweep(
                scope_slug=slug,
                finding_set_id=fact.finding_set_id if fact else None,
                produced_at=fact.produced_at if fact else None,
                revisions=dict(fact.revisions) if fact else {},
            )
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
    """The scopes a routine's last-swept table covers and the facts it lists: every
    declared scope not retired, plus a retired declared one only where a fact already
    records it swept. A scope unlinked since being swept never resurfaces through its
    own fact."""
    live_declared = sorted(slug for slug in declared if slug not in retired)
    return live_declared, [fact for fact in facts if fact.scope_slug in declared]


def declared_readings(declared: set[str], readings: list[MeasurementReading]) -> list[MeasurementReading]:
    """The `readings` of a scope the routine declares — a measurement of a scope since unlinked
    never joins the series."""
    return [reading for reading in readings if reading.scope_slug in declared]


class GardenSweepsService:
    """Reads a routine's last-swept table and measurement series, delegating the assembly
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
        declared = set(self._routine_scopes.list_scopes(routine.routine_id))
        live_declared, newest = sweep_coverage(
            declared,
            self._scopes.retired_slugs(routine.project_id),
            self._repo.last_swept_by_scope(routine.project_id, routine.name),
        )
        measurements = declared_readings(
            declared, self._repo.measurements_in_window(routine.project_id, routine.name, since=since, until=until)
        )
        return compute_sweeps(
            newest, measurements, routine_name=routine.name, scope_slugs=live_declared, since=since, until=until
        )
