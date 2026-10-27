"""A routine's finding trend read view — the trend, its periods, and its age split."""

from __future__ import annotations

from pydantic import BaseModel


class TrendPeriodView(BaseModel):
    period_start: str
    period_end: str
    created: int
    exits: dict[str, int]
    outflow: int
    withdrawn: int
    reopened: int


class TrendAgeView(BaseModel):
    #: The caller-supplied instant the window's created findings are split against.
    boundary: str
    recent: int
    older: int
    unattributed: int


class TrendView(BaseModel):
    routine_name: str
    since: str
    until: str
    period_days: int
    #: Fixed-length slices of ``[since, until)``, each ``period_days`` long.
    periods: list[TrendPeriodView]
    age: TrendAgeView
