"""The rule a subscription-usage window is admitted by — one definition, shared by both daemons."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeGuard

from blizzard.foundation.roles import domain_model
from blizzard.foundation.store.utc import as_utc, iso_utc

# A Unix timestamp past this many seconds is read as milliseconds.
_MILLISECONDS_ABOVE = 2e10


@domain_model
@dataclass(frozen=True)
class AdmittedUsageWindow:
    """One rate-limit window that passed :func:`admit_usage_window`, its reset read as UTC."""

    window: str
    utilization_pct: float
    resets_at: datetime
    window_seconds: int

    @property
    def stored(self) -> dict[str, object]:
        """The window's JSON object, its reset an ISO-8601 UTC instant."""
        return {
            "window": self.window,
            "utilization_pct": self.utilization_pct,
            "resets_at": iso_utc(self.resets_at),
            "window_seconds": self.window_seconds,
        }


def admit_usage_window(entry: object) -> AdmittedUsageWindow | str:
    """One window off a usage sample, or the field it is refused for: ``window`` a string,
    ``utilization_pct`` a finite number from 0 to 100, ``window_seconds`` a positive integer —
    a ``bool`` is never a number here — and ``resets_at`` an ISO-8601 instant or a Unix
    timestamp, read as UTC when naive (``bzh:utc-instants``)."""
    if not isinstance(entry, Mapping):
        return "not an object"
    window, pct, seconds = entry.get("window"), entry.get("utilization_pct"), entry.get("window_seconds")
    if not isinstance(window, str):
        return "window"
    if not _is_number(pct) or not math.isfinite(pct) or not 0 <= pct <= 100:
        return "utilization_pct"
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
        return "window_seconds"
    resets_at = _instant(entry.get("resets_at"))
    if resets_at is None:
        return "resets_at"
    return AdmittedUsageWindow(window=window, utilization_pct=float(pct), resets_at=resets_at, window_seconds=seconds)


def _is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _instant(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return as_utc(value)
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            try:
                return as_utc(datetime.fromisoformat(value))
            except ValueError:
                return None
    if not _is_number(value) or not math.isfinite(value):
        return None
    seconds = value / 1000 if abs(value) > _MILLISECONDS_ABOVE else value
    return datetime.fromtimestamp(seconds, UTC)
