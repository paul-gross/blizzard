"""The external-subscription-usage domain value (issue #218, phase 1).

How much of a metered plan's rolling windows an account has consumed, as the harness's
own account reports it — never derived from blizzard's own token/cost tallies.
:attr:`ExternalSubscriptionUsageWindow.utilization_pct` is **0-100, not 0-1**, despite
the fraction-shaped name the source API gives it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

__all__ = ["ExternalSubscriptionUsageSnapshot", "ExternalSubscriptionUsageWindow"]


@dataclass(frozen=True)
class ExternalSubscriptionUsageWindow:
    """One rate-limit window's utilization, as the harness's own account reports it.

    ``window`` is the harness-native label and ``window_seconds`` its length, carried
    alongside it; ``resets_at`` is the UTC-aware instant the counter resets."""

    window: str
    utilization_pct: float
    resets_at: datetime
    window_seconds: int


@dataclass(frozen=True)
class ExternalSubscriptionUsageSnapshot:
    """One sample of every window the harness's account reported at ``sampled_at``.

    ``sampled_at`` is the injected clock's instant, never a harness-reported time.
    ``windows`` holds one entry per window with usable data — never a fabricated zero."""

    sampled_at: datetime
    windows: tuple[ExternalSubscriptionUsageWindow, ...]
