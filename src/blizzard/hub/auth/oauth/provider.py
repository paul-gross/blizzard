"""``IOAuthProvider`` — the provider Protocol every conformer implements (issue #92).

The seam owns the whole authorize/exchange dance (``bzh:pluggable-seams``,
``bzh:deterministic-shell``): ``hub/api/auth_login.py`` calls only these two methods
and never touches ``httpx``/JWT/provider wire shapes directly — those live in
``internal/`` (``bzh:dependency-inversion``).
"""

from __future__ import annotations

from typing import Protocol

from blizzard.hub.auth.models import ProviderIdentity


class OAuthExchangeError(Exception):
    """Raised by :meth:`IOAuthProvider.exchange` on any failure to turn a presented
    ``code`` into a :class:`~blizzard.hub.auth.models.ProviderIdentity` — a rejected
    code, a network/transport failure, or a response the conformer cannot parse
    (including a bad/unknown ``id_token`` signature for the ``oidc`` conformer). The
    route's single catch site (``hub/api/auth_login.py``) turns this into a
    ``login_failed`` fact and a distinct error response; it never inspects the cause."""


class IOAuthProvider(Protocol):
    """One configured OAuth login provider, already bound to its client id/secret."""

    #: The configured provider name (identities key on this) — echoed for logging/facts.
    name: str
    #: The login button's label (``GET /api/auth/providers``).
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
