"""Builds the hub's OAuth provider registry from configuration (issue #92).

Mirrors ``hub/pm/internal/factory.py``'s own shape: one composition-root builder that
resolves each provider's ``client_secret_env`` from the environment (never
round-tripped through toml) and validates ``type``/``issuer`` *here*, at first
consumption — #91 only structurally parsed-and-carried the entries ("parse-and-carry
now so the schema is stable").
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import httpx

from blizzard.hub.auth.oauth.internal.github_provider import GithubProvider
from blizzard.hub.auth.oauth.internal.oidc_provider import OidcProvider
from blizzard.hub.auth.oauth.provider import IOAuthProvider
from blizzard.hub.auth.oauth.registry import OAuthProviderRegistry
from blizzard.hub.config import ConfigError, OAuthProviderConfig

_KNOWN_TYPES = {"oidc", "github"}


def build_oauth_registry(
    providers: Sequence[OAuthProviderConfig], *, http_client: httpx.Client | None = None
) -> OAuthProviderRegistry:
    """One provider conformer per configured entry, sharing one ``httpx.Client``.

    A misconfigured entry fails here, at boot, naming the offending provider — never
    silently at first login (mirrors ``build_pm_registry``'s own missing-``token_env``
    failure). ``http_client`` is injectable for tests; the ``host`` composition root
    leaves it unset for the real client."""
    client = http_client or httpx.Client(timeout=15.0)
    built: dict[str, IOAuthProvider] = {}
    for entry in providers:
        if entry.type not in _KNOWN_TYPES:
            raise ConfigError(
                f"[[auth.oauth.provider]] {entry.name!r} has unknown type {entry.type!r} "
                f"(known: {sorted(_KNOWN_TYPES)})"
            )
        if entry.client_secret_env not in os.environ:
            raise ConfigError(
                f"[[auth.oauth.provider]] {entry.name!r} names client_secret_env "
                f"{entry.client_secret_env!r}, which is unset"
            )
        secret = os.environ[entry.client_secret_env]
        if entry.type == "oidc":
            if not entry.issuer:
                raise ConfigError(f"[[auth.oauth.provider]] {entry.name!r} is type 'oidc' but carries no issuer")
            built[entry.name] = OidcProvider(
                name=entry.name,
                display_name=entry.display_name,
                issuer=entry.issuer,
                client_id=entry.client_id,
                client_secret=secret,
                http_client=client,
            )
        else:
            github_kwargs: dict[str, str] = {}
            if entry.api_base:
                github_kwargs = {"web_base": entry.api_base, "api_base": entry.api_base}
            built[entry.name] = GithubProvider(
                name=entry.name,
                display_name=entry.display_name,
                client_id=entry.client_id,
                client_secret=secret,
                http_client=client,
                **github_kwargs,  # type: ignore[arg-type]
            )
    return OAuthProviderRegistry(built)
