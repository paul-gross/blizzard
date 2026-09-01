"""A routine's per-scope last-swept table and its windowed measurement series — the
`GET /api/routines/{routine_id}/sweeps` read view.

Last-swept is unwindowed (D2); the measurement series is cut to `[since, until)`, the
same window `TrendView` reports over."""

from __future__ import annotations

from pydantic import BaseModel


class ScopeSweepView(BaseModel):
    scope_slug: str
    finding_set_id: str | None
    produced_at: str | None
    revisions: dict[str, str]


class MeasurementReadingView(BaseModel):
    scope_slug: str
    produced_at: str
    measurement: str


class GardenSweepsView(BaseModel):
    routine_name: str
    since: str
    until: str
    last_swept: list[ScopeSweepView]
    measurements: list[MeasurementReadingView]
