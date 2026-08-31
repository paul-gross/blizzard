"""A routine's finding trend — a read over a window's `finding_facts`, folded into
fixed-length periods with per-kind exit counts, the outflow/withdrawn roll-ups, and the
D5 introduced-age cut (blizzard#394 Phase 4). Periods are cut in Python, not SQL (D6,
`bzh:sql-portable`); the read never writes, and every count derives at read time
(`bzh:facts-not-status`)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from blizzard.hub.domain.findings import EXIT_KINDS, OUTFLOW_KINDS, WITHDRAWN_KINDS

#: A finding's birth, every way it leaves the live set, and its own undo — `observed`/
#: `gone` carry no trend meaning of their own.
TREND_FACT_KINDS = frozenset({"add", "reopened"}) | EXIT_KINDS


@dataclass(frozen=True)
class TrendFact:
    """One `finding_facts` row inside the window, joined to its own finding's
    `introduced_at` (D5) — the shape `facts_for_trend` returns."""

    kind: str
    recorded_at: datetime
    introduced_at: datetime | None


@dataclass(frozen=True)
class TrendPeriod:
    """One fixed-length slice of the window: findings created, exits per kind, the two
    roll-ups (D2) — `outflow` is `resolved` + `gone-confirmed`, `withdrawn` is the other
    three — and `reopened`, an exited finding's own undo, counted on its own so a
    resolve-reopen-resolve cycle reads as one creation, two exits, one reopen."""

    period_start: datetime
    period_end: datetime
    created: int
    exits: dict[str, int]
    outflow: int
    withdrawn: int
    reopened: int


@dataclass(frozen=True)
class TrendAgeCut:
    """The D5 cut over the window's created findings, against a caller-supplied
    `boundary` — `unattributed` is reported, never folded into `recent` or `older`."""

    boundary: datetime
    recent: int
    older: int
    unattributed: int


@dataclass(frozen=True)
class Trend:
    routine_name: str
    since: datetime
    until: datetime
    period_days: int
    periods: list[TrendPeriod]
    age: TrendAgeCut


class IReadGardenTrendRepository(Protocol):
    def facts_for_trend(self, routine_name: str, *, since: datetime, until: datetime) -> list[TrendFact]:
        """Every `add`/exit-kind fact for `routine_name` recorded in `[since, until)`
        (D6), each joined to its own finding's `introduced_at` (D5)."""
        ...


def _periods(since: datetime, until: datetime, period_days: int) -> list[tuple[datetime, datetime]]:
    step = timedelta(days=period_days)
    bounds = []
    start = since
    while start < until:
        end = min(start + step, until)
        bounds.append((start, end))
        start = end
    return bounds


def compute_trend(
    facts: list[TrendFact],
    *,
    routine_name: str,
    since: datetime,
    until: datetime,
    period_days: int,
    introduced_boundary: datetime,
) -> Trend:
    """Fold `facts` (already windowed at the store) into `period_days`-wide periods
    (D6) and the D5 age cut over the window's own `add` facts — one pass over `facts`,
    each bucketed by its own period index rather than rescanned per period per kind."""
    bounds = _periods(since, until, period_days)
    step = timedelta(days=period_days)
    created_counts = [0] * len(bounds)
    reopened_counts = [0] * len(bounds)
    exit_counts: list[dict[str, int]] = [dict.fromkeys(EXIT_KINDS, 0) for _ in bounds]
    recent = older = unattributed = 0
    for fact in facts:
        if fact.kind == "add":
            if fact.introduced_at is None:
                unattributed += 1
            elif fact.introduced_at >= introduced_boundary:
                recent += 1
            else:
                older += 1
        if bounds:
            index = min(int((fact.recorded_at - since) / step), len(bounds) - 1)
            if fact.kind == "add":
                created_counts[index] += 1
            elif fact.kind == "reopened":
                reopened_counts[index] += 1
            elif fact.kind in EXIT_KINDS:
                exit_counts[index][fact.kind] += 1
    periods = [
        TrendPeriod(
            period_start=period_start,
            period_end=period_end,
            created=created_counts[i],
            exits=dict(sorted(exit_counts[i].items())),
            outflow=sum(count for kind, count in exit_counts[i].items() if kind in OUTFLOW_KINDS),
            withdrawn=sum(count for kind, count in exit_counts[i].items() if kind in WITHDRAWN_KINDS),
            reopened=reopened_counts[i],
        )
        for i, (period_start, period_end) in enumerate(bounds)
    ]
    age = TrendAgeCut(boundary=introduced_boundary, recent=recent, older=older, unattributed=unattributed)
    return Trend(routine_name=routine_name, since=since, until=until, period_days=period_days, periods=periods, age=age)


class GardenTrendService:
    """Reads a routine's trend over a window, delegating the fold to `compute_trend` —
    the store hands over rows, this hands back the shape. `routine_name` is a query
    filter, like `list_for`'s own, not an entity resolved and passed by the caller; the
    route resolves existence at the edge before this is ever invoked."""

    def __init__(self, *, repo: IReadGardenTrendRepository) -> None:
        self._repo = repo

    def trend(
        self,
        routine_name: str,
        *,
        since: datetime,
        until: datetime,
        period_days: int,
        introduced_boundary: datetime,
    ) -> Trend:
        facts = self._repo.facts_for_trend(routine_name, since=since, until=until)
        return compute_trend(
            facts,
            routine_name=routine_name,
            since=since,
            until=until,
            period_days=period_days,
            introduced_boundary=introduced_boundary,
        )
