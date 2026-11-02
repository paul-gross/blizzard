"""Where an open submission stands on a flow it holds."""

from __future__ import annotations

from enum import StrEnum


class FlowPlaceState(StrEnum):
    """Where an open submission stands on a flow it holds."""

    LANDING = "landing"
    NEXT = "next"
    WAITING = "waiting"
    POLLING = "polling"
    AWAITING_MERGE = "awaiting-merge"
    PASSED_OVER = "passed-over"
