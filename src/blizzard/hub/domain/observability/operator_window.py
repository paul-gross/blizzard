"""The window an operator verb reads over — a trace replay or an egress backfill.

Both lanes bound it the same way: it must be non-empty, no wider than the lane's maximum, and
closed in the past, as an egress reset's ``to`` must be. Each lane names its own refusal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from blizzard.foundation.roles import domain_model


class WindowFault(StrEnum):
    INVERTED = "inverted"
    TOO_WIDE = "too_wide"
    FUTURE = "future"


@domain_model
@dataclass(frozen=True)
class OperatorWindow:
    """The half-open window ``[since, until)``."""

    since: datetime
    until: datetime

    def fault(self, *, max_window: timedelta, now: datetime) -> WindowFault | None:
        """The first bound the window breaks — inverted or empty, then too wide, then reaching
        past ``now`` — or ``None`` when it holds."""
        if self.until <= self.since:
            return WindowFault.INVERTED
        if self.until - self.since > max_window:
            return WindowFault.TOO_WIDE
        if self.until > now:
            return WindowFault.FUTURE
        return None


def fault_message(fault: WindowFault, *, max_window_name: str, max_window_seconds: int) -> str:
    """The operator-facing reason for ``fault``, naming the lane's maximum-width setting."""
    if fault is WindowFault.INVERTED:
        return "until must be after since"
    if fault is WindowFault.TOO_WIDE:
        return f"window is wider than {max_window_name} ({max_window_seconds} seconds)"
    return "until must not be in the future"
