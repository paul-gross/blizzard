"""The identity domain's value objects — ``User``, ``Identity``, ``Session``,
``ResolvedIdentity`` (issue #91).

``Role``/``Permission`` are imported from :mod:`blizzard.auth_core` (decision D3);
this module's only job is the hub-local *identity* shapes they attach to."""

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
    """What a provider conformer resolves a code exchange to (issue #92). Lives here,
    not under ``hub/auth/oauth/``, so the dependency arrow points from the adapter
    subpackage into the domain, never the reverse (``bzh:dependency-inversion``)."""

    subject: str
    handle: str
    email: str | None
    email_verified: bool


@dataclass(frozen=True)
class AuthStateEntry:
    """A single-use ``state`` row (decision D5) — the anti-CSRF/replay token round-tripped
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
    #: The user this row mints a session for (issue #96) — ``kind="cli_login"`` rows only.
    user_id: str | None = None


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
    target (issue #94; see ``hub/auth/bootstrap.py``). ``claimed_user_id`` is ``None``
    while no verified user has matched ``email`` yet."""

    email: str
    claimed_user_id: str | None
    updated_at: datetime


@dataclass(frozen=True)
class ResolvedIdentity:
    """The request principal a human-plane edge resolves to — ``hub/api/auth_session.py``'s
    ``resolve_identity``/``require()`` return this, never a bare ``User``. It carries the
    expanded permission set (computed once, by :func:`blizzard.auth_core.expand`) and
    ``display_name`` so no call site re-expands the role or re-reads the user row."""

    user_id: str
    username: str
    display_name: str
    role: Role
    permissions: frozenset[Permission]
