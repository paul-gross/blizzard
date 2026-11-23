"""The hub's configured OAuth providers, built into their registry — the one public door to the provider adapters."""

from __future__ import annotations

from collections.abc import Sequence

import httpx2

from blizzard.hub.auth.oauth.internal.factory import ProviderEntry
from blizzard.hub.auth.oauth.registry import IOAuthProviderRegistry
from blizzard.hub.config import OAuthProviderConfig


def configured_providers(
    providers: Sequence[OAuthProviderConfig], *, http_client: httpx2.Client
) -> IOAuthProviderRegistry:
    """One provider per configured entry, all sharing ``http_client``. A misconfigured entry fails here, naming the
    provider, so it surfaces at boot rather than at the first login."""
    return ProviderEntry.registry(providers, http_client=http_client)
