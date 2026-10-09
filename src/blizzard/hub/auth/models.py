"""The identity domain's value objects — ``User``, ``Identity``, ``Session``,
``ResolvedIdentity``.

``Role``/``Permission`` are imported from :mod:`blizzard.auth_core`;
this module's only job is the hub-local *identity* shapes they attach to."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.auth_core import Permission, Role
from blizzard.foundation.roles import domain_model
from blizzard.hub.tenancy import TenantId


@domain_model
@dataclass(frozen=True)
class User:
    """A hub-local account row. What the user may do lives in their memberships and the hub
    administrator facts, never on the account."""

    user_id: str
    username: str
    display_name: str
    email: str | None
    created_at: datetime


@domain_model
@dataclass(frozen=True)
class Identity:
    """One linked provider identity — ``(provider_name, subject)`` uniquely names it."""

    provider_name: str
    subject: str
    user_id: str
    handle: str
    created_at: datetime


@domain_model
@dataclass(frozen=True)
class Session:
    """A resolved session row — ``id_hash`` is the sha256 hex digest of the plaintext
    session id; the plaintext itself is never stored (mirrors
    ``runner_registrations.token_hash``)."""

    id_hash: str
    user_id: str
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime


@domain_model
@dataclass(frozen=True)
class ProviderIdentity:
    """What a provider conformer resolves a code exchange to. Lives here,
    not under ``hub/auth/oauth/``, so the dependency arrow points from the adapter
    subpackage into the domain, never the reverse (``bzh:dependency-inversion``)."""

    subject: str
    handle: str
    #: The account's primary address — the one a new user's row keeps and the email-merge
    #: rule matches on.
    email: str | None
    email_verified: bool
    #: Every address the provider reports as verified on the account, the primary among them.
    verified_emails: tuple[str, ...] = ()
    #: Whether the provider vouches for every address in ``verified_emails`` — only then may
    #: one of them admit the account through an email invitation. A provider that cannot say
    #: (a client's own directory, say) reports ``False``.
    emails_authoritative: bool = False


@domain_model
@dataclass(frozen=True)
class AuthStateEntry:
    """A single-use ``state`` row — the anti-CSRF/replay token round-tripped
    through a provider redirect. ``provider_name`` cross-checks the callback's own
    ``{name}`` path segment, so a state minted for one provider cannot be replayed
    against another's callback."""

    state: str
    kind: str
    provider_name: str
    return_to: str
    code_challenge: str | None
    created_at: datetime
    expires_at: datetime
    #: The user this row mints a session for — ``kind="cli_login"`` rows only.
    user_id: str | None = None


@domain_model
@dataclass(frozen=True)
class AuthFact:
    """One append-only row in ``auth_facts`` — the non-chunk-scoped
    security/audit event log (``bzh:facts-not-status``). ``actor`` is who/what triggered
    the event (a client IP for an unauthenticated failure); ``subject`` is what it concerns
    (a provider name); ``detail`` is free-form structured context."""

    kind: str
    actor: str
    subject: str
    detail: str
    recorded_at: datetime


@domain_model
@dataclass(frozen=True)
class SuperuserBootstrap:
    """The singleton row tracking the currently configured ``auth.superuser`` bootstrap
    target (see ``src/blizzard/hub/auth/bootstrap.py``). ``claimed_user_id`` is ``None``
    while no verified user has matched ``email`` yet."""

    email: str
    claimed_user_id: str | None
    updated_at: datetime


@domain_model
@dataclass(frozen=True)
class ResolvedIdentity:
    """The request principal a human-plane edge resolves to — ``src/blizzard/hub/api/auth_session.py``'s
    ``resolve_identity``/``require()`` return this, never a bare ``User``. It carries the
    expanded permission set (computed once, by :func:`blizzard.auth_core.permissions_for`) and
    ``display_name`` so no call site re-expands the role or re-reads the user row. ``role`` is the
    membership's role in ``tenant`` (``None`` without one); ``tenant`` is ``None`` only on hub-level
    routes."""

    user_id: str
    username: str
    display_name: str
    tenant: TenantId | None
    role: Role | None
    permissions: frozenset[Permission]
    hub_admin: bool


def wire_role(role: Role | None, *, hub_admin: bool) -> Role:
    """The single role a client is shown for a principal: ``superuser`` for a hub administrator,
    else the membership's role, else ``pending`` for no membership at all."""
    if hub_admin:
        return Role.SUPERUSER
    return role if role is not None else Role.PENDING
