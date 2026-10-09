"""``AuthService`` — mint/resolve/slide sessions, the first-login email-merge linking
rule, and collision-free username minting.

Holds the **write** repositories (``bzh:controller-read-only``) and takes already-loaded
objects (``bzh:domain-takes-objects``). Also holds the superuser claim, whose membership
grant is recorded via ``auth_facts`` in the same identity unit of work."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

from blizzard.auth_core import Role, permissions_for
from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.hub.auth import hub_admins, memberships
from blizzard.hub.auth.auth_state import IWriteAuthStateRepository
from blizzard.hub.auth.facts import AuthFactsService
from blizzard.hub.auth.hashing import SessionId
from blizzard.hub.auth.hub_admins import HubAdminFact, IReadHubAdminRepository
from blizzard.hub.auth.identities import IWriteIdentityRepository
from blizzard.hub.auth.identity_transaction import IIdentityTransactions
from blizzard.hub.auth.memberships import IReadMembershipRepository, MembershipFact
from blizzard.hub.auth.models import (
    AuthStateEntry,
    Identity,
    ProviderIdentity,
    ResolvedIdentity,
    Session,
    SuperuserBootstrap,
    User,
)
from blizzard.hub.auth.pkce import Pkce
from blizzard.hub.auth.sessions import IWriteSessionRepository
from blizzard.hub.auth.superuser_bootstrap import IWriteSuperuserBootstrapRepository
from blizzard.hub.auth.users import IWriteUserRepository
from blizzard.hub.tenancy import TenantId

_log = get_logger("blizzard.hub.auth")


#: Every role the role-assignment API may assign — ``superuser`` (the hub administrator) is
#: not one, and ``pending`` revokes the membership.
ASSIGNABLE_ROLES: tuple[Role, ...] = tuple(r for r in Role if r is not Role.SUPERUSER)


def principal(user: User, *, tenant: TenantId | None, role: Role | None, hub_admin: bool) -> ResolvedIdentity:
    """``user`` resolved for ``tenant``: its permissions expanded once, from the membership's role
    and the hub administrator role."""
    return ResolvedIdentity(
        user_id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        tenant=tenant,
        role=role,
        permissions=permissions_for(role, hub_admin),
        hub_admin=hub_admin,
    )


#: A session slides forward on every resolve by this much (idle timeout) — chosen as a
#: generous working-day window.
IDLE_TTL = timedelta(hours=24)

#: The absolute cap on a session's lifetime regardless of activity — a session minted
#: at T is never valid past ``T + ABSOLUTE_MAX_AGE``, even if touched continuously.
ABSOLUTE_MAX_AGE = timedelta(days=30)

#: The minimum expiry slide worth a write — a smaller slide skips the write outright.
TOUCH_GRANULARITY = timedelta(minutes=5)

#: ``secrets.token_urlsafe`` byte count for a minted ``state`` value.
STATE_BYTES = 24

#: How long a minted ``state`` stays redeemable — generous enough for a slow provider
#: redirect, short enough that an abandoned authorize attempt cannot be replayed later.
STATE_TTL = timedelta(minutes=10)

#: The ``auth_state.kind`` the provider-login dance writes.
PROVIDER_LOGIN_STATE_KIND = "provider_login"

#: The registered public client id the CLI authenticates as — a built-in
#: convention, not a per-user/per-runner registration.
CLI_CLIENT_ID = "cli"

#: The ``auth_state.kind`` a ``client=cli`` authorize mints — for this kind
#: the ``state`` column holds the minted authorization *code*, not an anti-CSRF value.
CLI_LOGIN_STATE_KIND = "cli_login"

#: ``secrets.token_urlsafe`` byte count for a minted CLI authorization code.
CLI_CODE_BYTES = 32

#: How long a minted CLI authorization code stays redeemable — short-lived, since the
#: loopback/paste exchange happens within seconds.
CLI_CODE_TTL = timedelta(minutes=5)

_SLUG_DISALLOWED = re.compile(r"[^a-z0-9-]+")


@domain_model
@dataclass(frozen=True)
class Slug:
    """A provider handle, reduced to the username base minting starts from."""

    handle: str

    @property
    def text(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        """Lowercase, disallowed chars collapsed to ``-``, trimmed — falling back to
        ``user`` for a handle that slugifies to nothing (one made entirely of symbols)."""
        slug = _SLUG_DISALLOWED.sub("-", self.handle.strip().lower()).strip("-")
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
        memberships: IReadMembershipRepository,
        hub_admins: IReadHubAdminRepository,
        transactions: IIdentityTransactions,
        idle_ttl: timedelta = IDLE_TTL,
        absolute_max_age: timedelta = ABSOLUTE_MAX_AGE,
        touch_granularity: timedelta = TOUCH_GRANULARITY,
    ) -> None:
        self._users = users
        self._identities = identities
        self._sessions = sessions
        self._auth_state = auth_state
        self._clock = clock
        self._superuser_bootstrap = superuser_bootstrap
        self._auth_facts = auth_facts
        self._memberships = memberships
        self._hub_admins = hub_admins
        self._transactions = transactions
        self._idle_ttl = idle_ttl
        self._absolute_max_age = absolute_max_age
        self._touch_granularity = touch_granularity

    def touch_session(self, session: Session, *, tenant: TenantId | None) -> ResolvedIdentity | None:
        """Slide ``session``'s expiry and resolve its owning user's identity for ``tenant``.

        ``None`` when idle-expired, past its absolute maximum age, or its user no
        longer exists (takes the already-loaded ``Session``). Skips the write when
        the elapsed time since ``last_seen_at`` is under ``touch_granularity``."""
        now = self._clock.now()
        if session.expires_at <= now:
            return None
        if now - session.created_at >= self._absolute_max_age:
            return None
        user = self._users.get(session.user_id)
        if user is None:
            return None
        # Elapsed time, not the expiry slide: the slide saturates at `absolute_max_age`
        # while the session is still live, which would freeze that delta at zero.
        if now - session.last_seen_at >= self._touch_granularity:
            new_expires_at = min(session.created_at + self._absolute_max_age, now + self._idle_ttl)
            self._sessions.touch(session.id_hash, last_seen_at=now, expires_at=new_expires_at)
        role = self._memberships.role_in(tenant, user.user_id) if tenant is not None else None
        return principal(user, tenant=tenant, role=role, hub_admin=self._hub_admins.is_hub_admin(user.user_id))

    def mint_session(self, user: User) -> tuple[str, Session]:
        """Mint a fresh session for ``user``; returns ``(plaintext_id, session)`` — only
        the hash is stored, so the plaintext is handed back exactly once (#92)."""
        plaintext = secrets.token_urlsafe(SessionId.BYTES)
        now = self._clock.now()
        session = Session(
            id_hash=SessionId(plaintext).hash,
            user_id=user.user_id,
            created_at=now,
            expires_at=now + self._idle_ttl,
            last_seen_at=now,
        )
        self._sessions.create(session)
        _log.info("session minted", user_id=user.user_id, username=user.username)
        return plaintext, session

    def link_or_mint(self, identity: ProviderIdentity, *, provider_name: str) -> User:
        """The first-login email-merge linking rule: resolve ``identity`` to the
        :class:`User` it belongs to, minting one if none exists.

        An existing ``(provider_name, subject)`` link wins, its ``handle`` refreshed in place;
        else a **verified** email match attaches; else a user with no membership is minted.
        Whichever user it resolves to claims a configured, still-unclaimed ``auth.superuser``
        whose email it holds."""
        existing_link = self._identities.get(provider_name, identity.subject)
        if existing_link is not None:
            user = self._users.get(existing_link.user_id)
            assert user is not None, f"identity {provider_name!r}:{identity.subject!r} references a missing user"
            if existing_link.handle != identity.handle:
                self._identities.update_handle(provider_name, identity.subject, handle=identity.handle)
            return self._claim_superuser(user)

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
                return self._claim_superuser(matched)

        user = User(
            user_id=Id.mint(IdPrefix.USER, self._clock).value,
            username=self.mint_username(identity.handle),
            display_name=identity.handle,
            email=identity.email if identity.email_verified else None,
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
        return self._claim_superuser(user)

    def revoke(self, session: Session) -> None:
        """Delete ``session`` outright — logout (#92)."""
        self._sessions.delete(session.id_hash)
        _log.info("session revoked", user_id=session.user_id)

    def start_state(self, *, kind: str, provider_name: str, return_to: str, ttl: timedelta = STATE_TTL) -> str:
        """Mint and persist a single-use ``state``; returns the plaintext
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

    # --- CLI login -----------------------------------------------

    def mint_cli_code(self, user: User, *, code_challenge: str, redirect_uri: str) -> str:
        """Mint a single-use authorization code for the CLI's PKCE exchange, for an
        already-resolved ``user``.

        Reuses the ``auth_state`` table — ``code_challenge`` and ``user_id``
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
        (a session, never a runner-style JWT), or ``None`` on any failure:
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
        if not Pkce(code_verifier).matches(entry.code_challenge):
            return None
        user = self._users.get(entry.user_id)
        if user is None:
            return None
        plaintext, _session = self.mint_session(user)
        return plaintext

    def mint_username(self, handle: str) -> str:
        """A collision-free username from a provider ``handle`` — the slug, or the slug
        with a numeric suffix appended once a collision is found."""
        base = Slug(handle).text
        candidate = base
        suffix = 1
        while self._users.username_exists(candidate):
            suffix += 1
            candidate = f"{base}-{suffix}"
        return candidate

    # --- superuser bootstrap -------------------------------------

    def get_superuser_bootstrap(self) -> SuperuserBootstrap | None:
        """The singleton bootstrap row's read passthrough, so a caller never reaches past
        the service into the repository (``bzh:controller-read-only``)."""
        return self._superuser_bootstrap.get()

    def record_superuser_bootstrap(self, *, email: str, claimed_user_id: str | None) -> None:
        """Replace the singleton bootstrap row, stamped from the injected clock, in an identity unit of work — a
        claim either commits before the replacement or reads the new row, never claiming the one it supersedes."""
        with self._transactions.begin() as writes:
            writes.bootstrap.upsert(
                SuperuserBootstrap(email=email, claimed_user_id=claimed_user_id, updated_at=self._clock.now())
            )

    def cancel_unclaimed_superuser_bootstrap(self) -> None:
        """Delete the bootstrap row while nobody has claimed it — an intent no person holds ends when
        ``auth.superuser`` stops naming it. A claimed row stays, and no hub administrator or membership is touched:
        configuration never revokes anyone. Read and delete share one identity unit of work, so a claim either
        commits first and keeps its row, or finds no row to claim."""
        with self._transactions.begin() as writes:
            current = writes.bootstrap.get()
            if current is not None and current.claimed_user_id is None:
                writes.bootstrap.clear()

    def report_superuser_bootstrap_unclaimed(self, *, email: str) -> None:
        """Surface a still-unclaimed bootstrap target — never a silent dead end."""
        self._auth_facts.superuser_bootstrap_unclaimed(email=email)

    def _claim_superuser(self, user: User) -> User:
        """Claim the configured ``auth.superuser`` for ``user`` when its email is the still-unclaimed
        target, in one identity unit of work: the claim, the hub administrator role (``bootstrap``),
        and — while the hub holds exactly one tenant and it has no admin — an ``admin`` membership
        there. A target already claimed is never claimed again. Returns ``user``."""
        if user.email is None or not _claimable(self._superuser_bootstrap.get(), user.email):
            return user
        now = self._clock.now()
        with self._transactions.begin() as writes:
            if not _claimable(writes.bootstrap.get(), user.email):
                return user
            writes.bootstrap.upsert(SuperuserBootstrap(email=user.email, claimed_user_id=user.user_id, updated_at=now))
            if not writes.hub_admins.is_hub_admin(user.user_id):
                writes.hub_admins.record(HubAdminFact(user.user_id, True, now, hub_admins.SET_BY_BOOTSTRAP))
            tenant = writes.memberships.only_tenant()
            if tenant is not None and writes.memberships.admin_count(tenant) == 0:
                from_role = writes.memberships.role_in(tenant, user.user_id)
                writes.memberships.record(
                    MembershipFact(tenant, user.user_id, Role.ADMIN, now, memberships.SET_BY_BOOTSTRAP)
                )
                self._auth_facts.user_role_changed(
                    actor="system",
                    subject=user.username,
                    tenant=tenant,
                    from_role=from_role,
                    to_role=Role.ADMIN,
                    into=writes.auth_facts,
                )
        _log.info("superuser claimed", user_id=user.user_id, email=user.email)
        return user


def _claimable(bootstrap: SuperuserBootstrap | None, email: str) -> bool:
    return bootstrap is not None and bootstrap.claimed_user_id is None and bootstrap.email == email
