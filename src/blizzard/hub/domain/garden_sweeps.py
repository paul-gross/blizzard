"""A routine's per-scope last-swept table and its windowed measurement series — a read
over `finding_sets`, each row joined to its own artifact's `produced_at`. Last-swept is
unwindowed (D2): a scope swept months ago must never read as never. The measurement
series is cut to `[since, until)`, the same window `garden_trend.py`'s own read reports
over; the cut is done in Python, not SQL (`bzh:sql-portable`), the same split
`garden_trend.py` makes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.scopes import IReadScopeRepository


@dataclass(frozen=True)
class SweepFact:
    """One `finding_sets` row for a routine, joined to its own artifact's `produced_at`
    — the shape `sweeps_for_routine` returns, unwindowed."""

    finding_set_id: str
    scope_slug: str
    produced_at: datetime
    revisions: dict[str, str]
    measurement: str | None


@dataclass(frozen=True)
class ScopeSweep:
    """One row of the last-swept table (D3, D4) — `finding_set_id`/`produced_at` `None`
    marks a scope this routine has never swept."""

    scope_slug: str
    finding_set_id: str | None
    produced_at: datetime | None
    revisions: dict[str, str]


@dataclass(frozen=True)
class MeasurementReading:
    """One recorded measurement inside the window (D5) — opaque text, never parsed."""

    scope_slug: str
    produced_at: datetime
    measurement: str


@dataclass(frozen=True)
class GardenSweeps:
    routine_name: str
    since: datetime
    until: datetime
    last_swept: list[ScopeSweep]
    measurements: list[MeasurementReading]


class IReadGardenSweepsRepository(Protocol):
    def sweeps_for_routine(self, routine_name: str) -> list[SweepFact]:
        """Every `finding_sets` row for `routine_name`, unwindowed (D2), each joined to
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
    every non-retired scope (D3) — and the windowed measurement series (D2, D5). One
    pass over `facts`: newest-per-scope by `produced_at`, ties broken by
    `finding_set_id` (ULID-monotonic, `garden_trend.py`'s own tie convention)."""
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


class GardenSweepsService:
    """Reads a routine's last-swept table and measurement series, delegating the fold
    to `compute_sweeps`. `routine_name` is a query filter, like
    `GardenTrendService.trend`'s own — existence is resolved at the edge, before this
    is ever invoked."""

    def __init__(self, *, repo: IReadGardenSweepsRepository, scopes: IReadScopeRepository) -> None:
        self._repo = repo
        self._scopes = scopes

    def sweeps(self, routine_name: str, *, since: datetime, until: datetime) -> GardenSweeps:
        facts = self._repo.sweeps_for_routine(routine_name)
        live_slugs = [scope.slug for scope in self._scopes.list_all() if not self._scopes.is_retired(scope.slug)]
        return compute_sweeps(facts, routine_name=routine_name, scope_slugs=live_slugs, since=since, until=until)
