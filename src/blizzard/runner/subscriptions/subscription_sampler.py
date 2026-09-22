"""The provider subscription-sampling seam (blizzard#436, issue #218).

A **pluggable, provider-selected** external-system seam (``bzh:pluggable-seams``): each
declared subscription is sampled through its own binding, selected by ``provider`` at
composition. :attr:`ExternalSubscriptionUsageWindow.utilization_pct` is **0-100, not
0-1**, despite the fraction-shaped name the source API gives it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

__all__ = [
    "PROVIDER_ANTHROPIC",
    "PROVIDER_OPENAI",
    "ExternalSubscriptionUsageSnapshot",
    "ExternalSubscriptionUsageWindow",
    "ISubscriptionSampler",
    "SampleMiss",
    "SampleMissReason",
]

# The Anthropic provider-sampler binding's own selector value (blizzard#436) — distinct
# from a subscription declaration's `slug`, which identifies an operator's subscription.
PROVIDER_ANTHROPIC = "anthropic"

# The OpenAI (ChatGPT plan) binding's selector — reached only by an explicit `[[subscription]]`.
PROVIDER_OPENAI = "openai"


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


class SampleMissReason(StrEnum):
    """The closed set of reasons one sampling attempt produced nothing (blizzard#504).

    ``CREDENTIAL_LAPSED`` is a token already past its own expiry before any request, or
    a 401 from the provider; ``CREDENTIAL_UNREADABLE`` is a missing, malformed, or
    incomplete credential file; ``ENDPOINT_UNREACHABLE`` is any other non-2xx response
    or a request-level failure (timeout, connection error); ``RESPONSE_UNPARSEABLE`` is
    a 2xx response whose body cannot be read as the windows it should carry."""

    CREDENTIAL_LAPSED = "credential_lapsed"
    CREDENTIAL_UNREADABLE = "credential_unreadable"
    ENDPOINT_UNREACHABLE = "endpoint_unreachable"
    RESPONSE_UNPARSEABLE = "response_unparseable"


@dataclass(frozen=True)
class SampleMiss:
    """One sampling attempt that produced nothing, with why (blizzard#504) — replaces a
    bare ``None``, so a lapsed credential is distinguishable from an unreachable endpoint
    or an unparseable body at every surface that reads a sampler's result."""

    reason: SampleMissReason


class ISubscriptionSampler(Protocol):
    """One declared subscription's rate-limit sampler. Dumb: samples, never decides."""

    def sample(self) -> ExternalSubscriptionUsageSnapshot | SampleMiss:
        """Sample this subscription's rate-limit utilization (issue #218).

        A :class:`SampleMiss` means this attempt produced nothing — a bad credential, an
        unreachable endpoint, an unparseable response — carrying its own closed-set
        reason. Never a raise: the sample is best-effort."""
        ...
