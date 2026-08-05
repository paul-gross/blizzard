"""``IOAuthProvider`` — the provider Protocol every conformer implements (issue #92).

The seam owns the whole authorize/exchange dance (``bzh:pluggable-seams``,
``bzh:deterministic-shell``); ``httpx``, JWT, and provider wire shapes live behind it
in ``internal/`` (``bzh:dependency-inversion``)."""

from __future__ import annotations

from typing import Protocol

from blizzard.hub.auth.models import ProviderIdentity


class OAuthExchangeError(Exception):
    """Raised by :meth:`IOAuthProvider.exchange` on any failure to turn a presented
    ``code`` into a :class:`~blizzard.hub.auth.models.ProviderIdentity` — a rejected
    code, a transport failure, or an unparseable response."""


class IOAuthProvider(Protocol):
    """One configured OAuth login provider, already bound to its client id/secret."""

    #: The configured provider name (identities key on this) — echoed for logging/facts.
    name: str
    #: The provider's human-facing display label.
    display_name: str
    #: ``"oidc"`` or ``"github"`` — echoed on ``GET /api/auth/providers`` so the (#93)
    #: web client can pick a mark.
    type: str

    def authorize_url(self, *, state: str, redirect_uri: str) -> str:
        """The URL ``GET /api/auth/{name}/authorize`` redirects the browser to."""
        ...

    def exchange(self, *, code: str, redirect_uri: str) -> ProviderIdentity:
        """Trade a presented authorization ``code`` for the provider's identity claim.

        Raises :class:`OAuthExchangeError` on any failure — a rejected code, a
        transport failure, or an unverifiable/unparseable response."""
        ...
