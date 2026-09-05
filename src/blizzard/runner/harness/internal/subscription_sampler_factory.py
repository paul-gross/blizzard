"""Selects the sampler binding for one declared provider subscription (blizzard#436).

A ``provider -> binding`` map, mirroring ``blizzard.hub.work_sources.internal.factory``'s
own selection shape; confined to ``internal/`` (``bzh:dependency-inversion``) so the seam
root stays free of both ``httpx`` and any one provider's binding. A provider with no known
binding selects ``None`` — declared, but unsampled, never a config-load failure: a runner
may name a provider before blizzard ships a binding for it."""

from __future__ import annotations

import httpx

from blizzard.foundation.clock import IClock
from blizzard.runner.config import SubscriptionDeclaration
from blizzard.runner.harness.internal.anthropic_subscription_sampler import (
    PROVIDER_ANTHROPIC,
    AnthropicSubscriptionSampler,
)
from blizzard.runner.harness.subscription_sampler import ISubscriptionSampler


def select_sampler(
    declaration: SubscriptionDeclaration,
    *,
    http_client: httpx.Client | None = None,
    clock: IClock | None = None,
) -> ISubscriptionSampler | None:
    """The sampler ``declaration.provider`` binds to, or ``None`` for an unknown provider."""
    if declaration.provider == PROVIDER_ANTHROPIC:
        return AnthropicSubscriptionSampler(
            credentials_path=declaration.credentials_path, http_client=http_client, clock=clock
        )
    return None
