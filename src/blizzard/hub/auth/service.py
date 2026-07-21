"""``AuthService`` — mint/resolve/slide sessions, the first-login email-merge linking
rule, and collision-free username minting (issues #91, #92).

Holds the **write** repositories (``bzh:controller-read-only`` — only the domain
writes) and takes already-loaded objects (``bzh:domain-takes-objects``): the edge
resolves a session-id hash to a :class:`~blizzard.hub.auth.models.Session` via the read
repo, then hands the object to :meth:`AuthService.touch_session` for the sliding-expiry
write and role expansion — no mutation happens at the edge
(``hub/api/auth_session.py``).

:meth:`mint_session` and :meth:`mint_username` landed in #91 (no login mechanism yet)
so the schema/service shape was stable ahead of #92, which is the first caller —
:meth:`link_or_mint` (the provider-login callback's own linking step, ``hub/api/
auth_login.py``) resolves a :class:`~blizzard.hub.auth.models.ProviderIdentity` to a
:class:`User`, minting one when no identity link (or verified-email match) exists.
"""

from __future__ import annotations

import re
import secrets
from datetime import timedelta

from blizzard.auth_core import Role, expand
from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import USER_PREFIX, mint
from blizzard.foundation.logging import get_logger
from blizzard.hub.auth.auth_state import IWriteAuthStateRepository
from blizzard.hub.auth.hashing import SESSION_ID_BYTES, hash_session_id
from blizzard.hub.auth.identities import IWriteIdentityRepository
from blizzard.hub.auth.models import AuthStateEntry, Identity, ProviderIdentity, ResolvedIdentity, Session, User
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

#: ``secrets.token_urlsafe`` byte count for a minted ``state`` value.
STATE_BYTES = 24

#: How long a minted ``state`` stays redeemable — generous enough for a slow provider
#: redirect, short enough that an abandoned authorize attempt cannot be replayed later.
STATE_TTL = timedelta(minutes=10)

#: The ``auth_state.kind`` this phase's provider-login dance writes (decision D5) — the
#: same table's ``kind`` column distinguishes #95's later hub-as-IdP authorize entries.
PROVIDER_LOGIN_STATE_KIND = "provider_login"

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
        auth_state: IWriteAuthStateRepository,
        clock: IClock,
        idle_ttl: timedelta = IDLE_TTL,
        absolute_max_age: timedelta = ABSOLUTE_MAX_AGE,
    ) -> None:
        self._users = users
        self._identities = identities
        self._sessions = sessions
        self._auth_state = auth_state
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

    def link_or_mint(self, identity: ProviderIdentity, *, provider_name: str) -> User:
        """The first-login email-merge linking rule (issue #92): resolve ``identity``
        (already exchanged by an :class:`~blizzard.hub.auth.oauth.provider.IOAuthProvider`
        conformer, ``bzh:domain-takes-objects``) to the :class:`User` it belongs to,
        minting one if none exists.

        Subject mapping wins on every later login: an existing ``(provider_name,
        identity.subject)`` link always resolves to its own user, and its stored
        ``handle`` is refreshed in place (a provider-side rename never re-mints a user).
        A **new** link with a **verified** email matching an existing user's email
        attaches to that user instead of minting one — an unverified email never
        merges, and (defensively) is never itself stored on a freshly minted user's
        ``email`` either, so a later verified login for the same address cannot merge
        into an account an unverified claim seeded (an unverified provider-reported
        email is not proof of ownership). Otherwise a new user is minted with
        ``role=guest`` and a username collision-suffixed from the handle."""
        existing_link = self._identities.get(provider_name, identity.subject)
        if existing_link is not None:
            user = self._users.get(existing_link.user_id)
            assert user is not None, f"identity {provider_name!r}:{identity.subject!r} references a missing user"
            if existing_link.handle != identity.handle:
                self._identities.update_handle(provider_name, identity.subject, handle=identity.handle)
            return user

        now = self._clock.now()
        if identity.email_verified and identity.email:
            matched = self._users.get_by_email(identity.email)
            if matched is not None:
                self._identities.link(
                    Identity(
                        provider_name=provider_name,
                        subject=identity.subject,
                        user_id=matched.user_id,
                        handle=identity.handle,
                        created_at=now,
                    )
                )
                return matched

        user = User(
            user_id=mint(USER_PREFIX, self._clock),
            username=self.mint_username(identity.handle),
            display_name=identity.handle,
            email=identity.email if identity.email_verified else None,
            role=Role.GUEST,
            created_at=now,
        )
        self._users.create(user)
        self._identities.link(
            Identity(
                provider_name=provider_name,
                subject=identity.subject,
                user_id=user.user_id,
                handle=identity.handle,
                created_at=now,
            )
        )
        return user

    def revoke(self, session: Session) -> None:
        """Delete ``session`` outright — logout (#92)."""
        self._sessions.delete(session.id_hash)
        _log.info("session revoked", user_id=session.user_id)

    def start_state(self, *, kind: str, provider_name: str, return_to: str, ttl: timedelta = STATE_TTL) -> str:
        """Mint and persist a single-use ``state`` (decision D5); returns the plaintext
        value ``GET /api/auth/{name}/authorize`` round-trips through the redirect."""
        state = secrets.token_urlsafe(STATE_BYTES)
        now = self._clock.now()
        self._auth_state.create(
            AuthStateEntry(
                state=state,
                kind=kind,
                provider_name=provider_name,
                return_to=return_to,
                code_challenge=None,
                created_at=now,
                expires_at=now + ttl,
            )
        )
        return state

    def consume_state(self, state: str) -> AuthStateEntry | None:
        """Read-and-delete a presented ``state`` (single-use); ``None`` when it never
        existed, was already consumed, or has clock-expired — the callback
        (``hub/api/auth_login.py``) treats every one of these identically (a bad
        ``state``, never a distinct error)."""
        entry = self._auth_state.consume(state)
        if entry is None:
            return None
        if entry.expires_at <= self._clock.now():
            return None
        return entry

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
