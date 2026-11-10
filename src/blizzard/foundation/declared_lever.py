"""The levers a runner names when it reports why it is declining work — one definition, shared by the runner and the
hub's wire."""

from __future__ import annotations

from enum import StrEnum


class DeclaredLever(StrEnum):
    #: The subscription floor.
    FLOOR = "floor"
    PACE = "pace"
    UTILIZATION = "utilization"
    BUDGET = "budget"
    SCHEDULE = "schedule"
