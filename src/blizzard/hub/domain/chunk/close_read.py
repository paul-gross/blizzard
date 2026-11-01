"""Public-safe facts for a chunk's work-reference close diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class CloseIntent:
    sequence: int
    source: str
    ref: str
    enqueued_at: datetime
    retired_at: datetime | None


@domain_model
@dataclass(frozen=True)
class CloseClosure:
    sequence: int
    source: str
    ref: str
    outcome: str
    recorded_at: datetime


@domain_model
@dataclass(frozen=True)
class CloseAttempt:
    """An attempt's identity and timing, without outbound request data."""

    sequence: int
    intent_id: int
    attempted_at: datetime
    outcome: str
    resume_at: datetime | None
