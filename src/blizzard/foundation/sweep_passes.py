"""How one requested sweep pass ended — the vocabulary the hub's sweep roster and its wire answer share."""

from __future__ import annotations

from enum import StrEnum


class SweepPassOutcome(StrEnum):
    """How one sweep pass ended."""

    COMPLETED = "completed"
    FAILED = "failed"
