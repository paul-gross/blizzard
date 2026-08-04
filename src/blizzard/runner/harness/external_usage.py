"""The external-subscription-usage domain value (issue #218, phase 1).

A harness that runs under a metered subscription exposes its own rate-limit window
utilization — how much of the plan's rolling windows this account has consumed. That
figure comes from the harness's own account, never from blizzard's own token/cost
tallies (:mod:`blizzard.runner.harness.usage`): two different questions, so this is a
sibling module, not an extension of ``UsageSample``.

:attr:`ExternalSubscriptionUsageWindow.utilization_pct` is **0-100, not 0-1** — a
deliberate near-miss against a fraction, called out here because the source API
reports a fraction-shaped field name (``utilization``) that is actually already a
percentage, and a caller that assumes the obvious 0-1 convention and multiplies by
100 a second time silently reports 100x the true value.

A window absent from the harness's response (not returned at all, or returned with
a null utilization/reset) is an **absent tuple entry** — never a fabricated
``utilization_pct=0.0`` entry, which would misreport "not tracked" as "empty".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

__all__ = ["ExternalSubscriptionUsageSnapshot", "ExternalSubscriptionUsageWindow"]


@dataclass(frozen=True)
class ExternalSubscriptionUsageWindow:
    """One rate-limit window's utilization, as the harness's own account reports it.

    ``window`` is the harness-native window label (``"5h"``/``"7d"`` for Claude
    Code's rolling 5-hour and 7-day windows). ``utilization_pct`` is 0-100 (see the
    module docstring). ``resets_at`` is the UTC-aware instant the window's usage
    counter resets. ``window_seconds`` is the window's length in seconds
    (``18000``/``604800`` for ``"5h"``/``"7d"``), carried alongside the label so a
    caller never has to hardcode the mapping back.
    """

    window: str
    utilization_pct: float
    resets_at: datetime
    window_seconds: int


@dataclass(frozen=True)
class ExternalSubscriptionUsageSnapshot:
    """One sample of every window the harness's account reported at ``sampled_at``.

    ``sampled_at`` is the UTC-aware instant the sample was taken (the injected
    clock's ``now()``, not a harness-reported time — the harness API carries no
    sample timestamp of its own). ``windows`` holds one entry per window the
    response actually reported usable data for; a window the account has none for
    is simply missing from the tuple, never a fabricated zero entry.
    """

    sampled_at: datetime
    windows: tuple[ExternalSubscriptionUsageWindow, ...]
