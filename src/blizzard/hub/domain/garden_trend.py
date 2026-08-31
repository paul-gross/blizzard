"""A routine's finding trend — a read over a window's `finding_facts`, folded into
fixed-length periods with per-kind exit counts, the outflow/withdrawn roll-ups, and the
D5 introduced-age cut (blizzard#394 Phase 4).

Periods are cut in Python, not SQL (D6, `bzh:sql-portable`) — the window bounds the rows
at the store, the fold over them happens here, the `derive_liveness` shape. The read
never writes; every count derives at read time (`bzh:facts-not-status`)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from blizzard.hub.domain.findings import EXIT_KINDS, OUTFLOW_KINDS, WITHDRAWN_KINDS

#: The fact kinds a trend counts — a finding's birth plus every way it leaves the live
#: set; `observed`/`gone`/`reopened` carry no trend meaning of their own. The store
#: adapter's own `facts_for_trend` filters on this.
TREND_FACT_KINDS = frozenset({"add"}) | EXIT_KINDS


@dataclass(frozen=True)
class TrendFact:
    """One `finding_facts` row inside the window, joined to its own finding's
    `introduced_at` (D5) — the shape `facts_for_trend` returns."""

    kind: str
    recorded_at: datetime
    introduced_at: datetime | None


@dataclass(frozen=True)
class TrendPeriod:
    """One fixed-length slice of the window: findings created, exits per kind, and the
    two roll-ups (D2) — `outflow` is `resolved` + `gone-confirmed`, `withdrawn` is the
    other three."""

    period_start: datetime
    period_end: datetime
    created: int
    exits: dict[str, int]
    outflow: int
    withdrawn: int


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
    (D6) and the D5 age cut over the window's own `add` facts."""
    periods = []
    for period_start, period_end in _periods(since, until, period_days):
        in_period = [f for f in facts if period_start <= f.recorded_at < period_end]
        created = sum(1 for f in in_period if f.kind == "add")
        exits = {kind: sum(1 for f in in_period if f.kind == kind) for kind in sorted(EXIT_KINDS)}
        outflow = sum(count for kind, count in exits.items() if kind in OUTFLOW_KINDS)
        withdrawn = sum(count for kind, count in exits.items() if kind in WITHDRAWN_KINDS)
        periods.append(
            TrendPeriod(
                period_start=period_start,
                period_end=period_end,
                created=created,
                exits=exits,
                outflow=outflow,
                withdrawn=withdrawn,
            )
        )
    created = [f for f in facts if f.kind == "add"]
    age = TrendAgeCut(
        boundary=introduced_boundary,
        recent=sum(1 for f in created if f.introduced_at is not None and f.introduced_at >= introduced_boundary),
        older=sum(1 for f in created if f.introduced_at is not None and f.introduced_at < introduced_boundary),
        unattributed=sum(1 for f in created if f.introduced_at is None),
    )
    return Trend(routine_name=routine_name, since=since, until=until, period_days=period_days, periods=periods, age=age)


class GardenTrendService:
    """Reads a routine's trend over a window, delegating the fold to `compute_trend`
    (`bzh:domain-takes-objects` — the store hands over rows, this hands back the shape)."""

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
