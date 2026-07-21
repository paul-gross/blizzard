"""``AuthService`` — mint/resolve/slide sessions and mint collision-free usernames
(issue #91).

Holds the **write** repositories (``bzh:controller-read-only`` — only the domain
writes) and takes already-loaded objects (``bzh:domain-takes-objects``): the edge
resolves a session-id hash to a :class:`~blizzard.hub.auth.models.Session` via the read
repo, then hands the object to :meth:`AuthService.touch_session` for the sliding-expiry
write and role expansion — no mutation happens at the edge
(``hub/api/auth_session.py``).

No login mechanism exists in this phase (#92 adds it): :meth:`mint_session` and
:meth:`mint_username` exist now so the schema/service shape is stable for it, but
nothing in #91 calls them outside of tests seeding a session directly.
"""

from __future__ import annotations

import re
import secrets
from datetime import timedelta

from blizzard.auth_core import expand
from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.hub.auth.hashing import SESSION_ID_BYTES, hash_session_id
from blizzard.hub.auth.identities import IWriteIdentityRepository
from blizzard.hub.auth.models import ResolvedIdentity, Session, User
from blizzard.hub.auth.sessions import IWriteSessionRepository
from blizzard.hub.auth.users import IWriteUserRepository

_log = get_logger("blizzard.hub.auth")

#: A session slides forward on every resolve by this much (idle timeout) — chosen as a
#: generous working-day window; #92/#96 may expose this as config once a login
#: mechanism exists to make it tunable in practice.
IDLE_TTL = timedelta(hours=24)

#: The absolute cap on a session's lifetime regardless of activity — a session minted
#: at T is never valid past ``T + ABSOLUTE_MAX_AGE``, even if touched continuously.
ABSOLUTE_MAX_AGE = timedelta(days=30)

_SLUG_DISALLOWED = re.compile(r"[^a-z0-9-]+")


def _slugify(handle: str) -> str:
    """A username base from a provider handle — lowercase, disallowed chars collapsed
    to ``-``, trimmed. Falls back to ``user`` for a handle that slugifies to nothing
    (e.g. one made entirely of symbols)."""
    slug = _SLUG_DISALLOWED.sub("-", handle.strip().lower()).strip("-")
    return slug or "user"


class AuthService:
    """Mint, resolve, and slide sessions; mint collision-free usernames."""

    def __init__(
        self,
        *,
        users: IWriteUserRepository,
        identities: IWriteIdentityRepository,
        sessions: IWriteSessionRepository,
        clock: IClock,
        idle_ttl: timedelta = IDLE_TTL,
        absolute_max_age: timedelta = ABSOLUTE_MAX_AGE,
    ) -> None:
        self._users = users
        self._identities = identities
        self._sessions = sessions
        self._clock = clock
        self._idle_ttl = idle_ttl
        self._absolute_max_age = absolute_max_age

    def touch_session(self, session: Session) -> ResolvedIdentity | None:
        """Slide ``session``'s expiry and resolve its owning user's identity.

        ``None`` when the session has already idle-expired, has crossed its absolute
        maximum age, or its user no longer exists — the edge
        (``hub/api/auth_session.py``) treats any of these as "no session", never a
        distinct error. Takes the already-loaded :class:`~blizzard.hub.auth.models.Session`
        (``bzh:domain-takes-objects``): the edge resolves the hash to the row before
        calling this."""
        now = self._clock.now()
        if session.expires_at <= now:
            return None
        if now - session.created_at >= self._absolute_max_age:
            return None
        user = self._users.get(session.user_id)
        if user is None:
            return None
        new_expires_at = min(session.created_at + self._absolute_max_age, now + self._idle_ttl)
        self._sessions.touch(session.id_hash, last_seen_at=now, expires_at=new_expires_at)
        return ResolvedIdentity(
            user_id=user.user_id,
            username=user.username,
            display_name=user.display_name,
            role=user.role,
            permissions=expand(user.role),
        )

    def mint_session(self, user: User) -> tuple[str, Session]:
        """Mint a fresh session for ``user``; returns ``(plaintext_id, session)`` — the
        caller (the login callback, #92) sets the plaintext into the cookie/bearer
        exactly once and keeps no other copy, mirroring
        ``RunnerEnrollmentService.enroll``."""
        plaintext = secrets.token_urlsafe(SESSION_ID_BYTES)
        now = self._clock.now()
        session = Session(
            id_hash=hash_session_id(plaintext),
            user_id=user.user_id,
            created_at=now,
            expires_at=now + self._idle_ttl,
            last_seen_at=now,
        )
        self._sessions.create(session)
        _log.info("session minted", user_id=user.user_id, username=user.username)
        return plaintext, session

    def revoke(self, session: Session) -> None:
        """Delete ``session`` outright — logout (#92)."""
        self._sessions.delete(session.id_hash)
        _log.info("session revoked", user_id=session.user_id)

    def mint_username(self, handle: str) -> str:
        """A collision-free username from a provider ``handle`` — the slug, or the slug
        with a numeric suffix appended once a collision is found."""
        base = _slugify(handle)
        candidate = base
        suffix = 1
        while self._users.username_exists(candidate):
            suffix += 1
            candidate = f"{base}-{suffix}"
        return candidate
