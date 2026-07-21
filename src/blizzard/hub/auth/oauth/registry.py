"""The provider registry — configured providers keyed by name (issue #92).

Built once at the composition root (``hub/auth/oauth/internal/factory.py``) from
``[[auth.oauth.provider]]``; ``hub/api/auth_login.py`` depends only on
:class:`IOAuthProviderRegistry`, never the concrete conformers.
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


def _conforms_oauth_provider_registry(x: OAuthProviderRegistry) -> IOAuthProviderRegistry:
    return x
