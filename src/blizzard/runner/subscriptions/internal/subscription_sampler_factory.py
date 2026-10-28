"""Selects the sampler binding for one declared provider subscription.

A ``provider -> binding`` map, confined to ``internal/`` (``bzh:dependency-inversion``) so
the seam root stays free of both ``httpx2`` and any one provider's binding. A provider with
no known binding selects ``None`` — declared, but unsampled, never a config-load failure."""

from __future__ import annotations

from collections.abc import Callable

import httpx2

from blizzard.foundation.clock import IClock
from blizzard.runner.subscriptions.internal.anthropic_subscription_sampler import (
    DEFAULT_USAGE_API_BASE as ANTHROPIC_USAGE_API_BASE,
)
from blizzard.runner.subscriptions.internal.anthropic_subscription_sampler import AnthropicSubscriptionSampler
from blizzard.runner.subscriptions.internal.openai_subscription_sampler import (
    DEFAULT_USAGE_API_BASE as OPENAI_USAGE_API_BASE,
)
from blizzard.runner.subscriptions.internal.openai_subscription_sampler import OpenAISubscriptionSampler
from blizzard.runner.subscriptions.subscription_sampler import (
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    ISubscriptionSampler,
    SubscriptionSource,
)


def select_sampler(
    declaration: SubscriptionSource,
    *,
    clock: IClock,
    http_client: Callable[[], httpx2.Client],
) -> ISubscriptionSampler | None:
    """The sampler ``declaration.provider`` binds to, or ``None`` for an unknown provider."""
    if declaration.provider == PROVIDER_ANTHROPIC:
        return AnthropicSubscriptionSampler(
            credentials_path=declaration.credentials_path,
            http_client=http_client,
            clock=clock,
            usage_api_base=declaration.usage_api_base or ANTHROPIC_USAGE_API_BASE,
        )
    if declaration.provider == PROVIDER_OPENAI:
        return OpenAISubscriptionSampler(
            credentials_path=declaration.credentials_path,
            http_client=http_client,
            clock=clock,
            usage_api_base=declaration.usage_api_base or OPENAI_USAGE_API_BASE,
        )
    return None
