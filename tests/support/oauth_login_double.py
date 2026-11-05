"""Helpers for the auth login api tests, shared with other test modules."""

from __future__ import annotations

from blizzard.auth_core import Role
from blizzard.hub.auth.models import ProviderIdentity, User
from blizzard.hub.auth.oauth.provider import OAuthExchangeError
from tests.support.scaffolding import HubHarness, seed_user


class FakeOAuthProvider:
    """A canned :class:`~blizzard.hub.auth.oauth.provider.IOAuthProvider` — resolves a
    fixed code -> :class:`ProviderIdentity` map; raises :class:`OAuthExchangeError` for
    an unrecognized code."""

    def __init__(self, *, name: str, display_name: str = "", type: str = "oidc") -> None:
        self.name = name
        self.display_name = display_name or name
        self.type = type
        self.codes: dict[str, ProviderIdentity] = {}
        self.authorize_calls: list[tuple[str, str]] = []

    def authorize_url(self, *, state: str, redirect_uri: str) -> str:
        self.authorize_calls.append((state, redirect_uri))
        return f"https://provider.example/{self.name}/authorize?state={state}"

    def exchange(self, *, code: str, redirect_uri: str) -> ProviderIdentity:
        identity = self.codes.get(code)
        if identity is None:
            raise OAuthExchangeError(f"unrecognized code {code!r}")
        return identity


def state_from_redirect(location: str) -> str:
    # `RedirectResponse` urlencodes the query string the fake's `authorize_url` built.
    return location.rsplit("state=", 1)[1]


#: Ada as an authoritative provider reports her, with the verified email :func:`known_ada` seeds her under.
ADA = ProviderIdentity(
    subject="1", handle="ada", email="ada@example.com", email_verified=True, emails_authoritative=True
)


def known_ada(hub: HubHarness) -> User:
    """Ada as a person the hub already knows — a user with no membership holding ``ADA``'s verified email, so her
    sign-in links to her under the email-merge rule rather than being refused as a stranger's."""
    return seed_user(hub, username="ada", role=Role.PENDING, email="ada@example.com")
