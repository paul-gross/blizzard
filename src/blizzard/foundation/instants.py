"""Guarded parsers from a vendor- or host-supplied value to a UTC-aware instant (``bzh:utc-instants``).

One rule for both daemons (``bzh:shared-kernel``): each function names the unit it reads and
returns ``None`` — never raises — for a ``bool``, a non-finite or out-of-range number, or an
unreadable string, so a seam documented as never raising stays so."""

from __future__ import annotations

import math
from datetime import UTC, datetime


def epoch_seconds_instant(value: object) -> datetime | None:
    """A Unix timestamp in seconds as a UTC-aware instant."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        # An int past float range raises OverflowError here, as does the conversion itself.
        if not math.isfinite(value):
            return None
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def epoch_millis_instant(value: object) -> datetime | None:
    """A Unix timestamp in milliseconds as a UTC-aware instant."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return epoch_seconds_instant(value / 1000)
    except OverflowError:
        return None


def iso_instant(value: object) -> datetime | None:
    """An ISO-8601 string as a UTC-aware instant, a naive one read as UTC."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    except (OverflowError, ValueError):
        return None
