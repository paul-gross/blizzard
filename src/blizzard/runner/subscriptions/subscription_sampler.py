"""The provider subscription-sampling seam.

A **pluggable, provider-selected** external-system seam (``bzh:pluggable-seams``): each
declared subscription is sampled through its own binding, selected by ``provider`` at
composition. :attr:`ExternalSubscriptionUsageWindow.utilization_pct` is **0-100, not
0-1**, despite the fraction-shaped name the source API gives it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from blizzard.foundation.roles import domain_model, dto
from blizzard.foundation.store.utc import as_utc
from blizzard.foundation.subscription_miss import SampleMissReason

__all__ = [
    "ANTHROPIC_DEFAULT_CREDENTIALS_PATH",
    "MISS_REASON_TEXT",
    "PROVIDER_ANTHROPIC",
    "PROVIDER_OPENAI",
    "ExternalSubscriptionUsageSnapshot",
    "ExternalSubscriptionUsageWindow",
    "ISubscriptionSampler",
    "SampleMiss",
    "SampleMissReason",
    "SubscriptionSource",
    "credential_lapsed",
    "sample_due",
]

# The Anthropic provider-sampler binding's own selector value — distinct
# from a subscription declaration's `slug`, which identifies an operator's subscription.
PROVIDER_ANTHROPIC = "anthropic"

# The credential file Claude Code's own login writes — shared by the Anthropic sampler and
# the Claude Code health probe, both of which read it.
ANTHROPIC_DEFAULT_CREDENTIALS_PATH = str(Path.home() / ".claude" / ".credentials.json")

# The OpenAI (ChatGPT plan) binding's selector — reached only by an explicit `[[subscription]]`.
PROVIDER_OPENAI = "openai"


@domain_model
@dataclass(frozen=True)
class ExternalSubscriptionUsageWindow:
    """One rate-limit window's utilization, as the provider's own account reports it.

    ``window`` is the provider-native label and ``window_seconds`` its length, carried
    alongside it; ``resets_at`` is the UTC-aware instant the counter resets."""

    window: str
    utilization_pct: float
    resets_at: datetime
    window_seconds: int

    def exhausted_pending(self, now: datetime) -> bool:
        """Fully used, with its reset still ahead of ``now`` — a window still holding a limit."""
        return self.utilization_pct >= 100.0 and self.resets_at > now


@dto
@dataclass(frozen=True)
class ExternalSubscriptionUsageSnapshot:
    """One sample of every window a declared subscription's account reported at ``sampled_at``.

    ``sampled_at`` is the injected clock's instant, never a provider-reported time.
    ``windows`` holds one entry per window with usable data — never a fabricated zero."""

    sampled_at: datetime
    windows: tuple[ExternalSubscriptionUsageWindow, ...]


# Operator-facing text per closed-set miss reason — what to do about it, not the machine word.
MISS_REASON_TEXT: dict[SampleMissReason, str] = {
    SampleMissReason.CREDENTIAL_LAPSED: "credential lapsed: log in again",
    SampleMissReason.CREDENTIAL_UNREADABLE: "credential unreadable",
    SampleMissReason.ENDPOINT_UNREACHABLE: "endpoint unreachable",
    SampleMissReason.RESPONSE_UNPARSEABLE: "response unparseable",
}


@dto
@dataclass(frozen=True)
class SampleMiss:
    """One sampling attempt that produced nothing, with why — replaces a
    bare ``None``, so a lapsed credential is distinguishable from an unreachable endpoint
    or an unparseable body at every surface that reads a sampler's result."""

    reason: SampleMissReason


class SubscriptionSource(Protocol):
    """What a binding selector reads off one declared subscription: the ``provider`` it binds
    to and the ``credentials_path`` override, if any."""

    @property
    def provider(self) -> str: ...

    @property
    def credentials_path(self) -> str | None: ...


class ISubscriptionSampler(Protocol):
    """One declared subscription's rate-limit sampler. Dumb: samples, never decides."""

    def sample(self) -> ExternalSubscriptionUsageSnapshot | SampleMiss:
        """Sample this subscription's rate-limit utilization.

        A :class:`SampleMiss` means this attempt produced nothing — a bad credential, an
        unreachable endpoint, an unparseable response — carrying its own closed-set
        reason. Never a raise: the sample is best-effort."""
        ...


def sample_due(last_attempt_at: datetime | None, now: datetime, interval_seconds: int) -> bool:
    """A declared subscription samples once its own interval has passed since its last attempt,
    or at once when it was never attempted."""
    return last_attempt_at is None or now - last_attempt_at >= timedelta(seconds=interval_seconds)


def credential_lapsed(expires_at: datetime, now: datetime) -> bool:
    """A credential at or past its own expiry has lapsed, so sampling spends no request on it."""
    return as_utc(expires_at) <= as_utc(now)
