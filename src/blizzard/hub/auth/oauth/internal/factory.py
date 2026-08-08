"""Builds the hub's OAuth provider registry from configuration (issue #92).

One composition-root builder that resolves each provider's ``client_secret_env`` from
the environment (never round-tripped through toml) and validates ``type``/``issuer``
here, at first consumption.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from blizzard.hub.auth.oauth.internal.github_provider import GithubProvider
from blizzard.hub.auth.oauth.internal.oidc_provider import OidcProvider
from blizzard.hub.auth.oauth.provider import IOAuthProvider
from blizzard.hub.auth.oauth.registry import OAuthProviderRegistry
from blizzard.hub.config import ConfigError, OAuthProviderConfig


@dataclass(frozen=True)
class ProviderEntry:
    """One ``[[auth.oauth.provider]]`` entry, resolved to the conformer it names."""

    config: OAuthProviderConfig

    @classmethod
    def of(cls, config: OAuthProviderConfig) -> ProviderEntry:
        kinds: dict[str, type[ProviderEntry]] = {"oidc": OidcEntry, "github": GithubEntry}
        kind = kinds.get(config.type)
        if kind is None:
            raise ConfigError(
                f"[[auth.oauth.provider]] {config.name!r} has unknown type {config.type!r} (known: {sorted(kinds)})"
            )
        return kind(config)

    @classmethod
    def registry(
        cls, providers: Sequence[OAuthProviderConfig], *, http_client: httpx.Client | None = None
    ) -> OAuthProviderRegistry:
        """One provider conformer per configured entry, sharing one ``httpx.Client``.

        A misconfigured entry fails here, at boot, naming the offending provider — never
        silently at first login. ``http_client`` is injectable for tests; the ``host``
        composition root leaves it unset for the real client."""
        client = http_client or httpx.Client(timeout=15.0)
        return OAuthProviderRegistry({e.name: cls.of(e).provider(client) for e in providers})

    @property
    def secret(self) -> str:
        if self.config.client_secret_env not in os.environ:
            raise ConfigError(
                f"[[auth.oauth.provider]] {self.config.name!r} names client_secret_env "
                f"{self.config.client_secret_env!r}, which is unset"
            )
        return os.environ[self.config.client_secret_env]

    def provider(self, client: httpx.Client) -> IOAuthProvider:
        raise NotImplementedError


class OidcEntry(ProviderEntry):
    def provider(self, client: httpx.Client) -> IOAuthProvider:
        secret = self.secret
        if not self.config.issuer:
            raise ConfigError(f"[[auth.oauth.provider]] {self.config.name!r} is type 'oidc' but carries no issuer")
        return OidcProvider(
            name=self.config.name,
            display_name=self.config.display_name,
            issuer=self.config.issuer,
            client_id=self.config.client_id,
            client_secret=secret,
            http_client=client,
        )


class GithubEntry(ProviderEntry):
    def provider(self, client: httpx.Client) -> IOAuthProvider:
        bases: dict[str, str] = {}
        if self.config.api_base:
            bases = {"web_base": self.config.api_base, "api_base": self.config.api_base}
        return GithubProvider(
            name=self.config.name,
            display_name=self.config.display_name,
            client_id=self.config.client_id,
            client_secret=self.secret,
            http_client=client,
            **bases,  # type: ignore[arg-type]
        )
