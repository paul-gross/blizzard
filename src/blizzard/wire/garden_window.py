"""Wire models for garden window."""

from typing import Final, Literal

from blizzard.wire.problem import ProblemDetails

GARDEN_WINDOW_INVALID: Final = "urn:blizzard:problem:garden-window-invalid"


class GardenWindowInvalidProblem(ProblemDetails[Literal["urn:blizzard:problem:garden-window-invalid"]]):
    """A time window is malformed: an instant that is not ISO-8601, or a span the window refuses."""


GARDEN_WINDOW_PROBLEM_TYPES: frozenset[str] = frozenset({GARDEN_WINDOW_INVALID})
