"""``AuthService`` — mint/resolve/slide sessions, the first-login email-merge linking
rule, and collision-free username minting (issues #91, #92).

Holds the **write** repositories (``bzh:controller-read-only``) and takes already-loaded
objects (``bzh:domain-takes-objects``). Also holds :meth:`assign_role` (issue #94) and
the superuser-bootstrap primitives; every role change is recorded via ``auth_facts``."""

from __future__ import annotations

import re
import secrets
from datetime import timedelta

from blizzard.auth_core import Role, expand
from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import USER_PREFIX, mint
from blizzard.foundation.logging import get_logger
from blizzard.hub.auth.auth_state import IWriteAuthStateRepository
from blizzard.hub.auth.facts import AuthFactsService
from blizzard.hub.auth.hashing import SESSION_ID_BYTES, hash_session_id
from blizzard.hub.auth.identities import IWriteIdentityRepository
from blizzard.hub.auth.models import (
    AuthStateEntry,
    Identity,
    ProviderIdentity,
    ResolvedIdentity,
    Session,
    SuperuserBootstrap,
    User,
)
from blizzard.hub.auth.pkce import verify_code_challenge
from blizzard.hub.auth.sessions import IWriteSessionRepository
from blizzard.hub.auth.superuser_bootstrap import IWriteSuperuserBootstrapRepository
from blizzard.hub.auth.users import IWriteUserRepository

_log = get_logger("blizzard.hub.auth")


class RoleAssignmentRefused(Exception):
    """A role-change request violated a hub-side rule (issue #94) — self-change,
    ``superuser`` grant/revoke by a non-``superuser`` actor, or ``superuser`` itself,
    which is bootstrap-only and not assignable through the API."""


#: A session slides forward on every resolve by this much (idle timeout) — chosen as a
#: generous working-day window.
IDLE_TTL = timedelta(hours=24)

#: The absolute cap on a session's lifetime regardless of activity — a session minted
#: at T is never valid past ``T + ABSOLUTE_MAX_AGE``, even if touched continuously.
ABSOLUTE_MAX_AGE = timedelta(days=30)

#: ``secrets.token_urlsafe`` byte count for a minted ``state`` value.
STATE_BYTES = 24

#: How long a minted ``state`` stays redeemable — generous enough for a slow provider
#: redirect, short enough that an abandoned authorize attempt cannot be replayed later.
STATE_TTL = timedelta(minutes=10)

#: The ``auth_state.kind`` the provider-login dance writes (decision D5).
PROVIDER_LOGIN_STATE_KIND = "provider_login"

#: The registered public client id the CLI authenticates as (issue #96) — a built-in
#: convention, not a per-user/per-runner registration.
CLI_CLIENT_ID = "cli"

#: The ``auth_state.kind`` a ``client=cli`` authorize mints (issue #96) — for this kind
#: the ``state`` column holds the minted authorization *code*, not an anti-CSRF value.
CLI_LOGIN_STATE_KIND = "cli_login"

#: ``secrets.token_urlsafe`` byte count for a minted CLI authorization code.
CLI_CODE_BYTES = 32

#: How long a minted CLI authorization code stays redeemable — short-lived, since the
#: loopback/paste exchange happens within seconds.
CLI_CODE_TTL = timedelta(minutes=5)

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
        superuser_bootstrap: IWriteSuperuserBootstrapRepository,
        auth_facts: AuthFactsService,
        idle_ttl: timedelta = IDLE_TTL,
        absolute_max_age: timedelta = ABSOLUTE_MAX_AGE,
    ) -> None:
        self._users = users
        self._identities = identities
        self._sessions = sessions
        self._auth_state = auth_state
        self._clock = clock
        self._superuser_bootstrap = superuser_bootstrap
        self._auth_facts = auth_facts
        self._idle_ttl = idle_ttl
        self._absolute_max_age = absolute_max_age

    def touch_session(self, session: Session) -> ResolvedIdentity | None:
        """Slide ``session``'s expiry and resolve its owning user's identity.

        ``None`` when the session has already idle-expired, has crossed its absolute
        maximum age, or its user no longer exists. Takes the already-loaded
        :class:`~blizzard.hub.auth.models.Session` (``bzh:domain-takes-objects``)."""
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
        """Mint a fresh session for ``user``; returns ``(plaintext_id, session)`` — only
        the hash is stored, so the plaintext is handed back exactly once (#92)."""
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
        """The first-login email-merge linking rule (issue #92): resolve ``identity`` to the
        :class:`User` it belongs to, minting one if none exists.

        An existing ``(provider_name, subject)`` link wins, its ``handle`` refreshed in place;
        else a **verified** email match attaches; else a ``role=pending`` user is minted."""
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
            role=Role.PENDING,
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
        return self._maybe_claim_superuser_bootstrap(user)

    def revoke(self, session: Session) -> None:
        """Delete ``session`` outright — logout (#92)."""
        self._sessions.delete(session.id_hash)
        _log.info("session revoked", user_id=session.user_id)

    def start_state(self, *, kind: str, provider_name: str, return_to: str, ttl: timedelta = STATE_TTL) -> str:
        """Mint and persist a single-use ``state`` (decision D5); returns the plaintext
        value to round-trip through the redirect."""
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
        existed, was already consumed, or has clock-expired — indistinguishably."""
        entry = self._auth_state.consume(state)
        if entry is None:
            return None
        if entry.expires_at <= self._clock.now():
            return None
        return entry

    # --- CLI login (issue #96) -----------------------------------------------

    def mint_cli_code(self, user: User, *, code_challenge: str, redirect_uri: str) -> str:
        """Mint a single-use authorization code for the CLI's PKCE exchange, for an
        already-resolved ``user`` (issue #96).

        Reuses the ``auth_state`` table (decision D5) — ``code_challenge`` and ``user_id``
        are the two fields no other ``kind`` populates. The returned code is opaque."""
        code = secrets.token_urlsafe(CLI_CODE_BYTES)
        now = self._clock.now()
        self._auth_state.create(
            AuthStateEntry(
                state=code,
                kind=CLI_LOGIN_STATE_KIND,
                provider_name=CLI_CLIENT_ID,
                return_to=redirect_uri,
                code_challenge=code_challenge,
                created_at=now,
                expires_at=now + CLI_CODE_TTL,
                user_id=user.user_id,
            )
        )
        return code

    def exchange_cli_code(self, code: str, *, code_verifier: str, redirect_uri: str) -> str | None:
        """Redeem a code minted by :meth:`mint_cli_code` for a fresh hub session token
        (decision D6 — a session, never a runner-style JWT), or ``None`` on any failure:
        unknown/consumed/expired code, mismatched ``redirect_uri``, or a bad PKCE verifier.
        Every failure collapses to the same ``None``, so a caller cannot fingerprint which
        check failed (pinned by ``tests/test_cli_login_api.py``)."""
        entry = self._auth_state.consume(code)
        if entry is None or entry.kind != CLI_LOGIN_STATE_KIND:
            return None
        if entry.expires_at <= self._clock.now():
            return None
        if entry.return_to != redirect_uri:
            return None
        if entry.code_challenge is None or entry.user_id is None:
            return None
        if not verify_code_challenge(code_verifier, entry.code_challenge):
            return None
        user = self._users.get(entry.user_id)
        if user is None:
            return None
        plaintext, _session = self.mint_session(user)
        return plaintext

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

    # --- role assignment (issue #94) -----------------------------------------

    def assign_role(self, *, actor: ResolvedIdentity, subject: User, to_role: Role) -> User:
        """Enforce the hub-side role-change rules (issue #94), then apply the change.

        Raises :class:`RoleAssignmentRefused` when the actor is the subject, when either
        role is ``superuser`` (bootstrap-only), or when a non-``superuser`` actor grants or
        revokes ``admin``. A no-op request returns ``subject`` unchanged, recording no fact."""
        if actor.user_id == subject.user_id:
            raise RoleAssignmentRefused("cannot change your own role")
        if subject.role is Role.SUPERUSER or to_role is Role.SUPERUSER:
            raise RoleAssignmentRefused("superuser is not assignable through the API (bootstrap-only)")
        touches_admin = subject.role is Role.ADMIN or to_role is Role.ADMIN
        if touches_admin and actor.role is not Role.SUPERUSER:
            raise RoleAssignmentRefused("only superuser may grant or revoke admin")
        if subject.role is to_role:
            return subject
        return self._apply_role_change(subject, to_role, actor_username=actor.username)

    def _apply_role_change(self, user: User, to_role: Role, *, actor_username: str) -> User:
        """Write the role change and record the ``user_role_changed`` fact — the one
        place either :meth:`assign_role` or a bootstrap promote/demote lands a role
        write, so the durable row and the fact can never drift apart."""
        self._users.update_role(user.user_id, to_role)
        self._auth_facts.user_role_changed(
            actor=actor_username, subject=user.username, from_role=user.role, to_role=to_role
        )
        return User(
            user_id=user.user_id,
            username=user.username,
            display_name=user.display_name,
            email=user.email,
            role=to_role,
            created_at=user.created_at,
        )

    # --- superuser bootstrap (issue #94) -------------------------------------

    def get_superuser_bootstrap(self) -> SuperuserBootstrap | None:
        """The singleton bootstrap row's read passthrough, so a caller never reaches past
        the service into the repository (``bzh:controller-read-only``)."""
        return self._superuser_bootstrap.get()

    def record_superuser_bootstrap(self, *, email: str, claimed_user_id: str | None) -> None:
        """Replace the singleton bootstrap row, stamped from the injected clock."""
        self._superuser_bootstrap.upsert(
            SuperuserBootstrap(email=email, claimed_user_id=claimed_user_id, updated_at=self._clock.now())
        )

    def clear_superuser_bootstrap(self) -> None:
        """Delete the singleton bootstrap row outright — ``auth.superuser`` was unset."""
        self._superuser_bootstrap.clear()

    def bootstrap_apply_role(self, user: User, to_role: Role) -> User:
        """A system-driven role change outside :meth:`assign_role`'s rule engine — the
        superuser bootstrap's promote/demote. Recorded with ``actor="system"``."""
        return self._apply_role_change(user, to_role, actor_username="system")

    def report_superuser_bootstrap_unclaimed(self, *, email: str) -> None:
        """Surface a still-unclaimed bootstrap target — never a silent dead end."""
        self._auth_facts.superuser_bootstrap_unclaimed(email=email)

    def _maybe_claim_superuser_bootstrap(self, user: User) -> User:
        """Called only on :meth:`link_or_mint`'s newly-minted-user branch — the one branch
        a pre-provisioned, still-unclaimed bootstrap target can first resolve through.

        Promotes and marks the row claimed when ``user``'s (already-verified) email matches
        an unclaimed target; otherwise returns ``user`` unchanged."""
        if user.email is None:
            return user
        bootstrap = self._superuser_bootstrap.get()
        if bootstrap is None or bootstrap.claimed_user_id is not None or bootstrap.email != user.email:
            return user
        promoted = self.bootstrap_apply_role(user, Role.SUPERUSER)
        self.record_superuser_bootstrap(email=bootstrap.email, claimed_user_id=user.user_id)
        return promoted
