"""``select_renewer`` — the provider -> renewer-binding selection (blizzard#504).

Anthropic and any unknown provider select ``None`` — declared, but with no renewal
binding, which keeps today's read-only behaviour exactly (D2)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.config import SubscriptionDeclaration
from blizzard.runner.subscriptions.internal.credential_renewer_factory import select_renewer
from blizzard.runner.subscriptions.internal.openai_credential_renewer import OpenAICredentialRenewer
from blizzard.runner.subscriptions.one_shot_process import OneShotResult
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC, PROVIDER_OPENAI

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


class _UnreachableSubprocess:
    def run(
        self, argv: Sequence[str], *, stdin: str, timeout: float, env: Mapping[str, str] | None = None
    ) -> OneShotResult:
        raise AssertionError("no subprocess should have been run")


def test_anthropic_selects_no_renewer() -> None:
    declaration = SubscriptionDeclaration(slug="anthropic", name="Anthropic", provider=PROVIDER_ANTHROPIC)

    assert select_renewer(declaration, clock=FixedClock(_NOW), subprocess=_UnreachableSubprocess()) is None


def test_an_unknown_provider_selects_no_renewer() -> None:
    declaration = SubscriptionDeclaration(slug="mystery", name="Mystery Plan", provider="some-unshipped-provider")

    assert select_renewer(declaration, clock=FixedClock(_NOW), subprocess=_UnreachableSubprocess()) is None


def test_the_openai_provider_selects_an_openai_renewer_carrying_its_credentials_path() -> None:
    declaration = SubscriptionDeclaration(
        slug="codex", name="Codex", provider=PROVIDER_OPENAI, credentials_path="/tmp/some-auth.json"
    )

    renewer = select_renewer(declaration, clock=FixedClock(_NOW), subprocess=_UnreachableSubprocess())
    assert isinstance(renewer, OpenAICredentialRenewer)
