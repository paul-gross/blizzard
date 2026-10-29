"""The provider registry — configured providers keyed by name.

:class:`IOAuthProviderRegistry` looks a configured provider up by name, never exposing the concrete conformers.
"""

from __future__ import annotations

from typing import Protocol

from blizzard.hub.auth.oauth.provider import IOAuthProvider


class IOAuthProviderRegistry(Protocol):
    """Read-only lookup over the configured providers."""

    def get(self, name: str) -> IOAuthProvider | None: ...
    def list(self) -> list[IOAuthProvider]: ...


class OAuthProviderRegistry:
    """A plain name-keyed provider map, in configured order."""

    def __init__(self, providers: dict[str, IOAuthProvider]) -> None:
        self._providers = providers

    def get(self, name: str) -> IOAuthProvider | None:
        return self._providers.get(name)

    def list(self) -> list[IOAuthProvider]:
        return list(self._providers.values())


_oauth_provider_registry_conforms: type[IOAuthProviderRegistry] = OAuthProviderRegistry
