"""The identity domain's value objects — ``User``, ``Identity``, ``Session``,
``ResolvedIdentity`` (issue #91).

``Role``/``Permission`` are imported from :mod:`blizzard.auth_core` (decision D3), not
redefined here — this module's only job is the hub-local *identity* shapes those
values attach to.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.auth_core import Permission, Role


@dataclass(frozen=True)
class User:
    """A hub-local account row."""

    user_id: str
    username: str
    display_name: str
    email: str | None
    role: Role
    created_at: datetime


@dataclass(frozen=True)
class Identity:
    """One linked provider identity — ``(provider_name, subject)`` uniquely names it."""

    provider_name: str
    subject: str
    user_id: str
    handle: str
    created_at: datetime


@dataclass(frozen=True)
class Session:
    """A resolved session row — ``id_hash`` is the sha256 hex digest of the plaintext
    session id; the plaintext itself is never stored (mirrors
    ``runner_registrations.token_hash``, issue #86a)."""

    id_hash: str
    user_id: str
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True)
class ProviderIdentity:
    """What an :class:`~blizzard.hub.auth.oauth.provider.IOAuthProvider` conformer
    resolves a code exchange to (issue #92) — the shape both the ``oidc`` and
    ``github`` conformers normalize onto, and the domain's own
    :meth:`~blizzard.hub.auth.service.AuthService.link_or_mint` input. Lives here
    (not under ``hub/auth/oauth/``) so the domain linking rule depends on it without
    reaching into the adapter subpackage — the dependency arrow points the other way
    (an ``IOAuthProvider`` conformer imports this from the domain, ``bzh:dependency-inversion``)."""

    subject: str
    handle: str
    email: str | None
    email_verified: bool


@dataclass(frozen=True)
class AuthStateEntry:
    """A single-use ``state`` row (decision D5) — the anti-CSRF/replay token round-tripped
    through a provider redirect. ``authorize`` writes one; ``callback`` reads-and-deletes
    it (single-use), clock-expired. ``provider_name`` cross-checks the callback's own
    ``{name}`` path segment so a state minted for one provider cannot be replayed against
    another's callback. ``code_challenge`` is unused in this phase — reserved for #96's
    PKCE public client, which reuses this same table."""

    state: str
    kind: str
    provider_name: str
    return_to: str
    code_challenge: str | None
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class AuthFact:
    """One append-only row in ``auth_facts`` (issue #92) — the non-chunk-scoped
    security/audit event log (``bzh:facts-not-status``). ``actor`` is who/what triggered
    the event (a client IP for an unauthenticated failure); ``subject`` is what it concerns
    (a provider name); ``detail`` is free-form structured context."""

    kind: str
    actor: str
    subject: str
    detail: str
    recorded_at: datetime


@dataclass(frozen=True)
class SuperuserBootstrap:
    """The singleton row tracking the currently configured ``auth.superuser`` bootstrap
    target (issue #94) — durable so a later boot (or a config change to a *different*
    email) can find the *previous* bootstrap target to demote, and so a login can check
    whether it has just claimed an unclaimed intent. ``claimed_user_id`` is ``None``
    while no verified user has matched ``email`` yet; set once one has, either
    promoted directly at boot (the user already existed) or claimed by the first
    matching verified login (``AuthService.link_or_mint``)."""

    email: str
    claimed_user_id: str | None
    updated_at: datetime


@dataclass(frozen=True)
class ResolvedIdentity:
    """The request principal a human-plane edge resolves to — ``hub/api/auth_session.py``'s
    ``resolve_identity``/``require()`` return this, never a bare ``User``, so a call
    site never re-expands the role itself (``bzh:domain-core`` — the expansion is
    computed once, by :func:`blizzard.auth_core.expand`). ``display_name`` rides along
    (rather than a second ``GET /api/me`` lookup against the user row) since
    ``AuthService.touch_session`` already loads the full ``User``."""

    user_id: str
    username: str
    display_name: str
    role: Role
    permissions: frozenset[Permission]
