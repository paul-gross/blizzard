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
