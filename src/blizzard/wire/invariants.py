from __future__ import annotations

from typing import Literal

from blizzard.wire.base import WireModel
from blizzard.wire.problem import ProblemDetails


class InvariantViolation(WireModel):
    #: The check's slug — an open vocabulary a client shows as it finds it.
    invariant: str
    detail: str


class InvariantsResponse(WireModel):
    #: Every breach the check found; empty when every invariant holds.
    violations: list[InvariantViolation]


class InvariantsInProgressProblem(ProblemDetails[Literal["urn:blizzard:problem:invariants-in-progress"]]):
    """Another invariant check is running on this hub. ``code`` is the released body's only member, kept beside the
    problem members for a client before problem details."""

    code: Literal["invariants_in_progress"] = "invariants_in_progress"
