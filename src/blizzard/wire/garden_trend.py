"""A routine's finding trend — the `GET /api/routines/trend` read view (blizzard#394
Phase 4).

Every count is a fold over the window's own `finding_facts`, never a stored rollup
(`bzh:facts-not-status`); periods are fixed-length slices of `[since, until)`, and `age`
is the D5 cut over the window's created findings against a caller-supplied boundary."""

from __future__ import annotations

from pydantic import BaseModel


class TrendPeriodView(BaseModel):
    period_start: str
    period_end: str
    created: int
    exits: dict[str, int]
    outflow: int
    withdrawn: int


class TrendAgeView(BaseModel):
    boundary: str
    recent: int
    older: int
    unattributed: int


class TrendView(BaseModel):
    routine_name: str
    since: str
    until: str
    period_days: int
    periods: list[TrendPeriodView]
    age: TrendAgeView
