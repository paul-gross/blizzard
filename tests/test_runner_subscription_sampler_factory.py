"""``select_sampler`` — the provider -> sampler-binding selection (blizzard#436).

A runner may declare a subscription for a provider blizzard ships no binding for yet;
selection reads that as "declared, but unsampled" rather than a config-load failure, so
this is unit-tested directly rather than only through a wired loop context."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock
from blizzard.runner.config import SubscriptionDeclaration
from blizzard.runner.subscriptions.internal.anthropic_subscription_sampler import AnthropicSubscriptionSampler
from blizzard.runner.subscriptions.internal.openai_subscription_sampler import OpenAISubscriptionSampler
from blizzard.runner.subscriptions.internal.subscription_sampler_factory import select_sampler
from blizzard.wire.facts import PROVIDER_ANTHROPIC, PROVIDER_OPENAI

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def test_the_anthropic_provider_selects_an_anthropic_sampler_carrying_its_credentials_path(tmp_path: Path) -> None:
    missing_credentials = tmp_path / "absent-creds.json"
    declaration = SubscriptionDeclaration(
        slug="anthropic", name="Anthropic", provider=PROVIDER_ANTHROPIC, credentials_path=str(missing_credentials)
    )

    sampler = select_sampler(declaration, clock=FixedClock(_NOW))
    assert isinstance(sampler, AnthropicSubscriptionSampler)

    # Observes the threaded-through path via the sampler's own behavior, never a private
    # attribute: it fails to read *this* file, not the default credentials location.
    with capture_logs() as logs:
        assert sampler.sample() is None
    assert any(log.get("path") == str(missing_credentials) for log in logs)


def test_the_openai_provider_selects_an_openai_sampler_carrying_its_credentials_path(tmp_path: Path) -> None:
    missing_credentials = tmp_path / "absent-auth.json"
    declaration = SubscriptionDeclaration(
        slug="codex", name="Codex", provider=PROVIDER_OPENAI, credentials_path=str(missing_credentials)
    )

    sampler = select_sampler(declaration, clock=FixedClock(_NOW))
    assert isinstance(sampler, OpenAISubscriptionSampler)

    # Observes the threaded-through path via the sampler's own behavior, never a private
    # attribute: it fails to read *this* file, not the default credentials location.
    with capture_logs() as logs:
        assert sampler.sample() is None
    assert any(log.get("path") == str(missing_credentials) for log in logs)


def test_an_unknown_provider_selects_no_sampler() -> None:
    declaration = SubscriptionDeclaration(slug="mystery", name="Mystery Plan", provider="some-unshipped-provider")

    assert select_sampler(declaration, clock=FixedClock(_NOW)) is None
