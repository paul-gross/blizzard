"""The gardening run's mode vocabulary — one definition, shared by both daemons."""

from __future__ import annotations

from enum import StrEnum


class RunMode(StrEnum):
    """How a routine run settles its delta baseline: ``full`` reports every finding, ``delta``
    only what changed since the recorded baseline."""

    FULL = "full"
    DELTA = "delta"
