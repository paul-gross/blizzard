"""``select_sampler`` — the provider -> sampler-binding selection (blizzard#436).

A runner may declare a subscription for a provider blizzard ships no binding for yet;
selection reads that as "declared, but unsampled" rather than a config-load failure, so
this is unit-tested directly rather than only through a wired loop context."""

from __future__ import annotations

import pytest

from blizzard.runner.config import SubscriptionDeclaration
from blizzard.runner.harness.internal.anthropic_subscription_sampler import (
    PROVIDER_ANTHROPIC,
    AnthropicSubscriptionSampler,
)
from blizzard.runner.harness.internal.subscription_sampler_factory import select_sampler

pytestmark = pytest.mark.unit


def test_the_anthropic_provider_selects_an_anthropic_sampler_carrying_its_credentials_path() -> None:
    declaration = SubscriptionDeclaration(
        slug="anthropic", name="Anthropic", provider=PROVIDER_ANTHROPIC, credentials_path="/tmp/creds.json"
    )

    sampler = select_sampler(declaration)

    assert isinstance(sampler, AnthropicSubscriptionSampler)
    assert sampler._credentials_path == "/tmp/creds.json"


def test_an_unknown_provider_selects_no_sampler() -> None:
    declaration = SubscriptionDeclaration(slug="mystery", name="Mystery Plan", provider="some-unshipped-provider")

    assert select_sampler(declaration) is None
