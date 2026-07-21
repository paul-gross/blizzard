"""``build_oauth_registry`` — provider construction, config validation, secret
resolution from the environment (unit tier, issue #92).
"""

from __future__ import annotations

import httpx
import pytest

from blizzard.hub.auth.oauth.internal.factory import build_oauth_registry
from blizzard.hub.auth.oauth.internal.github_provider import GithubProvider
from blizzard.hub.auth.oauth.internal.oidc_provider import OidcProvider
from blizzard.hub.config import ConfigError, OAuthProviderConfig

pytestmark = pytest.mark.unit


def _client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))


def test_build_oauth_registry_builds_a_conformer_per_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BZ_OAUTH_GITHUB_SECRET", "s1")
    monkeypatch.setenv("BZ_OAUTH_OIDC_SECRET", "s2")
    providers = (
        OAuthProviderConfig(
            name="github",
            type="github",
            display_name="GitHub",
            client_id="c1",
            client_secret_env="BZ_OAUTH_GITHUB_SECRET",
        ),
        OAuthProviderConfig(
            name="oidc-co",
            type="oidc",
            display_name="Example SSO",
            client_id="c2",
            client_secret_env="BZ_OAUTH_OIDC_SECRET",
            issuer="https://issuer.example.com",
        ),
    )
    registry = build_oauth_registry(providers, http_client=_client())

    github = registry.get("github")
    oidc = registry.get("oidc-co")
    assert isinstance(github, GithubProvider)
    assert isinstance(oidc, OidcProvider)
    assert {p.name for p in registry.list()} == {"github", "oidc-co"}


def test_build_oauth_registry_rejects_an_unknown_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BZ_SECRET", "s")
    providers = (
        OAuthProviderConfig(
            name="bogus", type="saml", display_name="Bogus", client_id="c", client_secret_env="BZ_SECRET"
        ),
    )
    with pytest.raises(ConfigError, match="unknown type"):
        build_oauth_registry(providers, http_client=_client())


def test_build_oauth_registry_rejects_oidc_with_no_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BZ_SECRET", "s")
    providers = (
        OAuthProviderConfig(
            name="oidc-co", type="oidc", display_name="SSO", client_id="c", client_secret_env="BZ_SECRET"
        ),
    )
    with pytest.raises(ConfigError, match="carries no issuer"):
        build_oauth_registry(providers, http_client=_client())


def test_build_oauth_registry_rejects_an_unset_secret_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BZ_OAUTH_GITHUB_SECRET", raising=False)
    providers = (
        OAuthProviderConfig(
            name="github",
            type="github",
            display_name="GitHub",
            client_id="c1",
            client_secret_env="BZ_OAUTH_GITHUB_SECRET",
        ),
    )
    with pytest.raises(ConfigError, match="BZ_OAUTH_GITHUB_SECRET"):
        build_oauth_registry(providers, http_client=_client())


def test_build_oauth_registry_is_empty_for_no_providers() -> None:
    registry = build_oauth_registry((), http_client=_client())
    assert registry.list() == []
    assert registry.get("anything") is None
