"""``select_sampler`` — the provider -> sampler-binding selection.

A runner may declare a subscription for a provider blizzard ships no binding for yet;
selection reads that as "declared, but unsampled" rather than a config-load failure, so
this is unit-tested directly rather than only through a wired loop context."""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock
from blizzard.runner.config import SubscriptionDeclaration
from blizzard.runner.subscriptions.internal.anthropic_subscription_sampler import AnthropicSubscriptionSampler
from blizzard.runner.subscriptions.internal.openai_subscription_sampler import OpenAISubscriptionSampler
from blizzard.runner.subscriptions.internal.subscription_sampler_factory import select_sampler
from blizzard.runner.subscriptions.subscription_sampler import (
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    SAMPLED_PROVIDERS,
    SampleMiss,
    SampleMissReason,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def _unreachable_client() -> httpx2.Client:
    """Passed in place of a real client: no test here reaches the network."""
    raise AssertionError("no HTTP client should have been built")


def test_the_anthropic_provider_selects_an_anthropic_sampler_carrying_its_credentials_path(tmp_path: Path) -> None:
    missing_credentials = tmp_path / "absent-creds.json"
    declaration = SubscriptionDeclaration(
        slug="anthropic", name="Anthropic", provider=PROVIDER_ANTHROPIC, credentials_path=str(missing_credentials)
    )

    sampler = select_sampler(declaration, clock=FixedClock(_NOW), http_client=_unreachable_client)
    assert isinstance(sampler, AnthropicSubscriptionSampler)

    # Observes the threaded-through path via the sampler's own behavior, never a private
    # attribute: it fails to read *this* file, not the default credentials location.
    with capture_logs() as logs:
        result = sampler.sample()
        assert isinstance(result, SampleMiss)
        assert result.reason == SampleMissReason.CREDENTIAL_UNREADABLE
    assert any(log.get("path") == str(missing_credentials) for log in logs)


def test_the_openai_provider_selects_an_openai_sampler_carrying_its_credentials_path(tmp_path: Path) -> None:
    missing_credentials = tmp_path / "absent-auth.json"
    declaration = SubscriptionDeclaration(
        slug="codex", name="Codex", provider=PROVIDER_OPENAI, credentials_path=str(missing_credentials)
    )

    sampler = select_sampler(declaration, clock=FixedClock(_NOW), http_client=_unreachable_client)
    assert isinstance(sampler, OpenAISubscriptionSampler)

    # Observes the threaded-through path via the sampler's own behavior, never a private
    # attribute: it fails to read *this* file, not the default credentials location.
    with capture_logs() as logs:
        result = sampler.sample()
        assert isinstance(result, SampleMiss)
        assert result.reason == SampleMissReason.CREDENTIAL_UNREADABLE
    assert any(log.get("path") == str(missing_credentials) for log in logs)


def test_an_unknown_provider_selects_no_sampler() -> None:
    declaration = SubscriptionDeclaration(slug="mystery", name="Mystery Plan", provider="some-unshipped-provider")

    assert select_sampler(declaration, clock=FixedClock(_NOW), http_client=_unreachable_client) is None


@pytest.mark.parametrize("provider", [PROVIDER_ANTHROPIC, PROVIDER_OPENAI, "some-unshipped-provider"])
def test_the_sampled_providers_are_exactly_those_selecting_a_sampler(provider: str, tmp_path: Path) -> None:
    declaration = SubscriptionDeclaration(
        slug="plan", name="Plan", provider=provider, credentials_path=str(tmp_path / "absent.json")
    )

    selected = select_sampler(declaration, clock=FixedClock(_NOW), http_client=_unreachable_client)

    assert (selected is not None) is (provider in SAMPLED_PROVIDERS)


def _live_credentials(provider: str, path: Path) -> Path:
    """A credentials file the provider's binding reads as live, so a sample proceeds to its request."""
    if provider == PROVIDER_ANTHROPIC:
        expires_at = int((_NOW + timedelta(days=5)).timestamp() * 1000)
        path.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok", "expiresAt": expires_at}}))
    else:
        claims = base64.urlsafe_b64encode(json.dumps({"exp": (_NOW + timedelta(days=5)).timestamp()}).encode())
        token = f"h.{claims.decode().rstrip('=')}.s"
        path.write_text(json.dumps({"tokens": {"access_token": token, "account_id": "acct"}}))
    return path


def _requested_url(declaration: SubscriptionDeclaration) -> str:
    """The URL the selected sampler calls on one sample, through a transport that answers 500."""
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(str(request.url))
        return httpx2.Response(500)

    transport = httpx2.MockTransport(handler)
    sampler = select_sampler(
        declaration, clock=FixedClock(_NOW), http_client=lambda: httpx2.Client(transport=transport)
    )
    assert sampler is not None
    sampler.sample()
    (url,) = seen
    return url


@pytest.mark.parametrize(
    ("provider", "default_origin", "path"),
    [
        (PROVIDER_ANTHROPIC, "https://api.anthropic.com", "/api/oauth/usage"),
        (PROVIDER_OPENAI, "https://chatgpt.com/backend-api", "/codex/usage"),
    ],
)
def test_a_declared_usage_api_base_replaces_the_provider_default_and_absent_keeps_it(
    provider: str, default_origin: str, path: str, tmp_path: Path
) -> None:
    credentials = str(_live_credentials(provider, tmp_path / "creds.json"))
    default = SubscriptionDeclaration(slug="s", name="S", provider=provider, credentials_path=credentials)
    overridden = replace(default, usage_api_base="http://127.0.0.1:9999/vendor")

    assert _requested_url(default) == f"{default_origin}{path}"
    assert _requested_url(overridden) == f"http://127.0.0.1:9999/vendor{path}"


def test_each_subscription_carries_its_own_usage_api_base(tmp_path: Path) -> None:
    credentials = str(_live_credentials(PROVIDER_ANTHROPIC, tmp_path / "creds.json"))
    first = SubscriptionDeclaration(
        slug="a", name="A", provider=PROVIDER_ANTHROPIC, credentials_path=credentials, usage_api_base="http://one.test"
    )
    second = replace(first, slug="b", usage_api_base="http://two.test")

    assert _requested_url(first).startswith("http://one.test/")
    assert _requested_url(second).startswith("http://two.test/")


def test_an_unknown_provider_with_a_usage_api_base_is_still_unsampled() -> None:
    declaration = SubscriptionDeclaration(
        slug="m", name="M", provider="some-unshipped-provider", usage_api_base="http://x.test"
    )

    assert select_sampler(declaration, clock=FixedClock(_NOW), http_client=_unreachable_client) is None
