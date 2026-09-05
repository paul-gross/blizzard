"""The provider subscription-sampling seam (blizzard#436, issue #218).

Sampling a harness's own metered-plan rate-limit utilization is a **pluggable,
provider-selected** external-system seam (``bzh:pluggable-seams``) — no longer folded onto
the coding-harness adapter it used to ride on (:class:`~blizzard.runner.harness.adapter.
IHarnessAdapter` carried it through issue #218's first landing). A runner may declare
several provider subscriptions; each is sampled through its own binding, selected by
``provider`` at composition (see ``internal/`` for the concrete Anthropic binding and the
selection factory).

:attr:`ExternalSubscriptionUsageWindow.utilization_pct` is **0-100, not 0-1**, despite the
fraction-shaped name the source API gives it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

__all__ = [
    "ExternalSubscriptionUsageSnapshot",
    "ExternalSubscriptionUsageWindow",
    "ISubscriptionSampler",
]


@dataclass(frozen=True)
class ExternalSubscriptionUsageWindow:
    """One rate-limit window's utilization, as the provider's own account reports it.

    ``window`` is the provider-native label and ``window_seconds`` its length, carried
    alongside it; ``resets_at`` is the UTC-aware instant the counter resets."""

    window: str
    utilization_pct: float
    resets_at: datetime
    window_seconds: int


@dataclass(frozen=True)
class ExternalSubscriptionUsageSnapshot:
    """One sample of every window a declared subscription's account reported at ``sampled_at``.

    ``sampled_at`` is the injected clock's instant, never a provider-reported time.
    ``windows`` holds one entry per window with usable data — never a fabricated zero."""

    sampled_at: datetime
    windows: tuple[ExternalSubscriptionUsageWindow, ...]


class ISubscriptionSampler(Protocol):
    """One declared subscription's rate-limit sampler. Dumb: samples, never decides."""

    def sample(self) -> ExternalSubscriptionUsageSnapshot | None:
        """Sample this subscription's rate-limit utilization (issue #218).

        ``None`` means this attempt produced nothing — a bad credential, an unreachable
        endpoint, an unparseable response, anything. Never a raise: the sample is
        best-effort."""
        ...
