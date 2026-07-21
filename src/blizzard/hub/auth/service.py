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

Issue #94 adds :meth:`assign_role` (the admin API's hub-side role-change rules) and the
superuser-bootstrap primitives ``hub/auth/bootstrap.py`` orchestrates at boot —
:meth:`get_superuser_bootstrap`/:meth:`record_superuser_bootstrap`/
:meth:`clear_superuser_bootstrap`/:meth:`bootstrap_apply_role`/
:meth:`report_superuser_bootstrap_unclaimed` — plus the first-login claim check
:meth:`link_or_mint` runs on its newly-minted-user branch (the only branch a
pre-provisioned, still-unclaimed bootstrap target can first resolve through). Every
role change, API-driven or bootstrap-driven, is recorded through the injected
``auth_facts`` (``bzh:controller-read-only`` extended to a second collaborating
service, mirroring ``FactIngestService(fleet=...)``'s own domain-service-takes-service
shape) — never at the edge, since the bootstrap claim has no request-level caller to
record it instead.
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
    ``superuser`` grant/revoke by a non-``superuser`` actor, or ``superuser`` itself (not
    assignable through the API, bootstrap-only). The API route (``hub/api/users.py``)
    maps this to ``403``."""


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

#: The registered public client id the CLI authenticates as (issue #96) — a built-in
#: convention, not a per-user/per-runner registration (mirrors #95's runner clients,
#: which *are* registered rows; ``cli`` never is).
CLI_CLIENT_ID = "cli"

#: The ``auth_state.kind`` a ``client=cli`` authorize mints (issue #96) — this table's
#: ``state`` column holds the minted authorization *code* for this kind, not a
#: round-tripped anti-CSRF value the way the other two kinds use it.
CLI_LOGIN_STATE_KIND = "cli_login"

#: ``secrets.token_urlsafe`` byte count for a minted CLI authorization code.
CLI_CODE_BYTES = 32

#: How long a minted CLI authorization code stays redeemable — short-lived (unlike the
#: 10-minute provider-dance ``state``): the loopback/paste exchange happens within
#: seconds of the browser completing the hub login.
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
        return self._maybe_claim_superuser_bootstrap(user)

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

    # --- CLI login (issue #96) -----------------------------------------------

    def mint_cli_code(self, user: User, *, code_challenge: str, redirect_uri: str) -> str:
        """Mint a single-use authorization code for the CLI's PKCE exchange, once
        ``GET /api/auth/authorize?client=cli`` has already resolved ``user`` (an
        existing hub session, or freshly minted by the #92 provider dance it bounced
        through). Reuses the same ``auth_state`` table/mechanism as ``start_state``
        (decision D5) — ``code_challenge`` and ``user_id`` are the two fields no other
        ``kind`` populates. The returned code is opaque; the redeeming exchange
        (:meth:`exchange_cli_code`) is the only place ``user_id`` and
        ``code_challenge`` are read back."""
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
        (decision D6 — a session, never a runner-style JWT), or ``None`` on any
        failure: an unknown/already-consumed/expired code, a ``redirect_uri`` that
        does not exact-match the one the code was minted for, or a PKCE verifier that
        does not hash to the stored challenge. Every failure is treated identically —
        the route (``hub/api/idp.py``) raises one undifferentiated 400, mirroring
        ``consume_state``'s own "bad state" uniformity, so a caller cannot fingerprint
        which check failed."""
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
        """Enforce the admin API's hub-side role-change rules, then apply the change
        (``hub/api/users.py``'s ``POST /api/users/{id}/role``, ``require("user:manage")``
        already gates the route). Raises :class:`RoleAssignmentRefused` (403) on a
        violation:

        * a user cannot change their own role;
        * ``superuser`` is not assignable through the API in either direction
          (granting it, or moving a stored ``superuser`` subject to anything else) — it
          is bootstrap-only;
        * only a ``superuser`` actor may grant or revoke ``admin`` (an ``admin`` actor
          may freely move a subject between ``guest``/``contributor``).

        A no-op request (``subject.role == to_role``) returns ``subject`` unchanged and
        records no fact — there was no change to record."""
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
        """The singleton bootstrap row's read passthrough — ``hub/auth/bootstrap.py``'s
        own boot-time orchestration reads through the service rather than reaching past
        it into the repository (``bzh:controller-read-only``)."""
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
        """A system-driven role change outside :meth:`assign_role`'s API rule engine —
        the superuser bootstrap's own boot-time promote/demote (``hub/auth/
        bootstrap.py``). Recorded with ``actor="system"`` in the emitted fact."""
        return self._apply_role_change(user, to_role, actor_username="system")

    def report_superuser_bootstrap_unclaimed(self, *, email: str) -> None:
        """Surface a still-unclaimed bootstrap target — never a silent dead end."""
        self._auth_facts.superuser_bootstrap_unclaimed(email=email)

    def _maybe_claim_superuser_bootstrap(self, user: User) -> User:
        """Called only on :meth:`link_or_mint`'s newly-minted-user branch — the one
        branch a *pre-provisioned, still-unclaimed* bootstrap target can first resolve
        through (a user :meth:`link_or_mint` instead *finds* already existed at the
        last boot, when the boot-time promotion in ``hub/auth/bootstrap.py`` would
        already have claimed it). Promotes and marks the row claimed when ``user``'s
        (already-verified, per :meth:`link_or_mint`'s own rule) email matches an
        unclaimed target; otherwise returns ``user`` unchanged."""
        if user.email is None:
            return user
        bootstrap = self._superuser_bootstrap.get()
        if bootstrap is None or bootstrap.claimed_user_id is not None or bootstrap.email != user.email:
            return user
        promoted = self.bootstrap_apply_role(user, Role.SUPERUSER)
        self.record_superuser_bootstrap(email=bootstrap.email, claimed_user_id=user.user_id)
        return promoted
