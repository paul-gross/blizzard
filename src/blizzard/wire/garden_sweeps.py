"""A routine's per-scope last-swept table and its windowed measurement series — the
`GET /api/routines/{routine_id}/sweeps` read view.

Last-swept is unwindowed; the measurement series is cut to `[since, until)`, the
same window `TrendView` reports over."""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel

from blizzard.wire.problem import ProblemDetails


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


SWEEP_NOT_FOUND: Final = "urn:blizzard:problem:sweep-not-found"


SWEEP_SERVES_NO_TENANT: Final = "urn:blizzard:problem:sweep-serves-no-tenant"


class SweepNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:sweep-not-found"]]):
    """No sweep this hub builds goes by the name."""


class SweepServesNoTenantProblem(ProblemDetails[Literal["urn:blizzard:problem:sweep-serves-no-tenant"]]):
    """The sweep runs over every tenant only, so a single tenant's pass is refused."""


GARDEN_SWEEPS_PROBLEM_TYPES: frozenset[str] = frozenset({SWEEP_NOT_FOUND, SWEEP_SERVES_NO_TENANT})
