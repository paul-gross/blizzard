"""Which moment of an errand's last run a cadence's spacing counts from."""

from __future__ import annotations

from enum import StrEnum


class CadenceAnchor(StrEnum):
    """Which moment of an errand's last run its spacing counts from."""

    SCHEDULED = "scheduled"
    STARTED = "started"
    FINISHED = "finished"
