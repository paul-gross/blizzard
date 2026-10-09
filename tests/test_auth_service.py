"""``AuthService`` — mint/resolve/slide sessions, mint collision-free usernames (unit
tier).

Exercised against in-memory fakes of the repository Protocols and the identity unit of
work; the real SQLAlchemy adapters are exercised at component tier
(``tests/test_auth_repositories.py``).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from blizzard.auth_core import MEMBERSHIP_ROLES, Role, permissions_for
from blizzard.foundation.clock import FixedClock
from blizzard.hub.auth.facts import AuthFactsService
from blizzard.hub.auth.hashing import SessionId
from blizzard.hub.auth.hub_admins import HubAdminFact
from blizzard.hub.auth.identity_transaction import IdentityWrites
from blizzard.hub.auth.memberships import Membership, MembershipFact
from blizzard.hub.auth.models import (
    AuthFact,
    AuthStateEntry,
    Identity,
    ProviderIdentity,
    ResolvedIdentity,
    Session,
    SuperuserBootstrap,
    User,
)
from blizzard.hub.auth.service import PROVIDER_LOGIN_STATE_KIND, AuthService, RoleAssignmentRefused, principal
from blizzard.hub.tenancy import TenantId

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_TENANT = TenantId("ten_one")


class _FakeUserRepository:
    def __init__(self) -> None:
        self.by_id: dict[str, User] = {}

    def get(self, user_id: str) -> User | None:
        return self.by_id.get(user_id)

    def get_by_username(self, username: str) -> User | None:
        return next((u for u in self.by_id.values() if u.username == username), None)

    def get_by_email(self, email: str) -> User | None:
        return next((u for u in self.by_id.values() if u.email == email), None)

    def username_exists(self, username: str) -> bool:
        return self.get_by_username(username) is not None

    def get_many(self, user_ids: Sequence[str]) -> dict[str, User]:
        return {uid: self.by_id[uid] for uid in user_ids if uid in self.by_id}

    def list_all(self) -> list[User]:
        return sorted(self.by_id.values(), key=lambda u: u.created_at)

    def create(self, user: User) -> None:
        self.by_id[user.user_id] = user


class _FakeIdentityRepository:
    def __init__(self) -> None:
        self.rows: list[Identity] = []

    def get(self, provider_name: str, subject: str) -> Identity | None:
        return next((i for i in self.rows if i.provider_name == provider_name and i.subject == subject), None)

    def list_for_user(self, user_id: str) -> list[Identity]:
        return [i for i in self.rows if i.user_id == user_id]

    def list_for_users(self, user_ids: Sequence[str]) -> dict[str, list[Identity]]:
        grouped: dict[str, list[Identity]] = {}
        for row in self.rows:
            if row.user_id in user_ids:
                grouped.setdefault(row.user_id, []).append(row)
        return grouped

    def distinct_provider_names(self) -> set[str]:
        return {i.provider_name for i in self.rows}

    def link(self, identity: Identity) -> None:
        self.rows.append(identity)

    def update_handle(self, provider_name: str, subject: str, *, handle: str) -> None:
        for i, row in enumerate(self.rows):
            if row.provider_name == provider_name and row.subject == subject:
                self.rows[i] = Identity(
                    provider_name=row.provider_name,
                    subject=row.subject,
                    user_id=row.user_id,
                    handle=handle,
                    created_at=row.created_at,
                )
                return


class _FakeSessionRepository:
    def __init__(self) -> None:
        self.by_hash: dict[str, Session] = {}
        #: Every ``touch`` invocation, for tests asserting a granularity-skipped write.
        self.touch_calls: list[tuple[str, datetime, datetime]] = []

    def get_by_hash(self, id_hash: str) -> Session | None:
        return self.by_hash.get(id_hash)

    def create(self, session: Session) -> None:
        self.by_hash[session.id_hash] = session

    def touch(self, id_hash: str, *, last_seen_at: datetime, expires_at: datetime) -> None:
        self.touch_calls.append((id_hash, last_seen_at, expires_at))
        session = self.by_hash[id_hash]
        self.by_hash[id_hash] = Session(
            id_hash=session.id_hash,
            user_id=session.user_id,
            created_at=session.created_at,
            expires_at=expires_at,
            last_seen_at=last_seen_at,
        )

    def delete(self, id_hash: str) -> None:
        self.by_hash.pop(id_hash, None)


class _FakeAuthStateRepository:
    def __init__(self) -> None:
        self.by_state: dict[str, AuthStateEntry] = {}

    def get(self, state: str) -> AuthStateEntry | None:
        return self.by_state.get(state)

    def create(self, entry: AuthStateEntry) -> None:
        self.by_state[entry.state] = entry

    def consume(self, state: str) -> AuthStateEntry | None:
        return self.by_state.pop(state, None)


class _FakeSuperuserBootstrapRepository:
    def __init__(self) -> None:
        self.row: SuperuserBootstrap | None = None

    def get(self) -> SuperuserBootstrap | None:
        return self.row

    def upsert(self, bootstrap: SuperuserBootstrap) -> None:
        self.row = bootstrap

    def clear(self) -> None:
        self.row = None


class _FakeAuthFactsRepository:
    def __init__(self) -> None:
        self.rows: list[AuthFact] = []

    def list_recent(self, *, limit: int = 50) -> list[AuthFact]:
        return list(reversed(self.rows))[:limit]

    def create(self, fact: AuthFact) -> None:
        self.rows.append(fact)


class _FakeMembershipRepository:
    def __init__(self) -> None:
        self.facts: list[MembershipFact] = []
        self.tenants: list[TenantId] = [_TENANT]

    def _in_force(self) -> dict[tuple[str, str], Role | None]:
        return {(f.tenant_id, f.user_id): f.role for f in self.facts}

    def role_in(self, tenant: TenantId, user_id: str) -> Role | None:
        return self._in_force().get((tenant, user_id))

    def memberships_for(self, user_id: str) -> list[Membership]:
        return [m for m in self._memberships() if m.user_id == user_id]

    def members(self, tenant: TenantId) -> list[Membership]:
        return [m for m in self._memberships() if m.tenant_id == tenant]

    def admin_count(self, tenant: TenantId) -> int:
        return sum(1 for m in self.members(tenant) if m.role is Role.ADMIN)

    def only_tenant(self) -> TenantId | None:
        return self.tenants[0] if len(self.tenants) == 1 else None

    def record(self, fact: MembershipFact) -> None:
        self.facts.append(fact)

    def _memberships(self) -> list[Membership]:
        return [Membership(TenantId(t), u, r) for (t, u), r in self._in_force().items() if r is not None]


class _FakeHubAdminRepository:
    def __init__(self) -> None:
        self.facts: list[HubAdminFact] = []

    def is_hub_admin(self, user_id: str) -> bool:
        return user_id in self.holders()

    def holders(self) -> list[str]:
        return sorted(user_id for user_id, granted in {f.user_id: f.granted for f in self.facts}.items() if granted)

    def record(self, fact: HubAdminFact) -> None:
        self.facts.append(fact)


class _FakeTransactions:
    """Hands out the same fakes as every unit of work's writes — the fakes commit as they go."""

    def __init__(self, writes: IdentityWrites) -> None:
        self.writes = writes

    @contextmanager
    def begin(self) -> Iterator[IdentityWrites]:
        yield self.writes


class _Identity:
    """The membership and hub administrator fakes one service reads, and what seeds them."""

    def __init__(self, users: _FakeUserRepository) -> None:
        self.users = users
        self.memberships = _FakeMembershipRepository()
        self.hub_admins = _FakeHubAdminRepository()

    def seed(self, user_id: str, username: str, role: Role, *, email: str | None = None) -> User:
        """A user standing for ``role``: ``superuser`` an admin member and hub administrator, ``pending`` neither."""
        user = User(user_id=user_id, username=username, display_name=username, email=email, created_at=_T0)
        self.users.create(user)
        membership = Role.ADMIN if role is Role.SUPERUSER else role
        if membership in MEMBERSHIP_ROLES:
            self.memberships.record(MembershipFact(_TENANT, user_id, membership, _T0, "migration"))
        if role is Role.SUPERUSER:
            self.hub_admins.record(HubAdminFact(user_id, True, _T0, "migration"))
        return user


_IDENTITIES: dict[int, _Identity] = {}


def _identities(service: AuthService) -> _Identity:
    return _IDENTITIES[id(service)]


def _service(
    clock: FixedClock, **kwargs: object
) -> tuple[
    AuthService,
    _FakeUserRepository,
    _FakeSessionRepository,
    _FakeIdentityRepository,
    _FakeSuperuserBootstrapRepository,
    AuthFactsService,
]:
    users = _FakeUserRepository()
    identities = _FakeIdentityRepository()
    sessions = _FakeSessionRepository()
    auth_state = _FakeAuthStateRepository()
    superuser_bootstrap = _FakeSuperuserBootstrapRepository()
    facts = _FakeAuthFactsRepository()
    auth_facts = AuthFactsService(facts=facts, clock=clock)
    identity = _Identity(users)
    writes = IdentityWrites(
        users=users,
        identities=identities,
        sessions=sessions,
        memberships=identity.memberships,
        hub_admins=identity.hub_admins,
        bootstrap=superuser_bootstrap,
        auth_facts=facts,
    )
    service = AuthService(
        users=users,
        identities=identities,
        sessions=sessions,
        auth_state=auth_state,
        clock=clock,
        superuser_bootstrap=superuser_bootstrap,
        auth_facts=auth_facts,
        memberships=identity.memberships,
        hub_admins=identity.hub_admins,
        transactions=_FakeTransactions(writes),
        **kwargs,  # type: ignore[arg-type]
    )
    _IDENTITIES[id(service)] = identity
    return service, users, sessions, identities, superuser_bootstrap, auth_facts


def _user(user_id: str = "usr_1") -> User:
    return User(user_id=user_id, username="ada", display_name="Ada", email=None, created_at=_T0)


# --- mint_session -------------------------------------------------------------


def test_mint_session_stores_only_the_hash_never_the_plaintext() -> None:
    clock = FixedClock(_T0)
    service, _, sessions, _ident, _, _ = _service(clock)
    user = _user()

    plaintext, session = service.mint_session(user)

    assert session.id_hash == SessionId(plaintext).hash
    assert session.id_hash != plaintext
    assert sessions.by_hash[session.id_hash] is session


def test_mint_session_sets_expiry_from_idle_ttl() -> None:
    clock = FixedClock(_T0)
    idle_ttl = timedelta(hours=2)
    service, _, _, _ident, _, _ = _service(clock, idle_ttl=idle_ttl)
    _, session = service.mint_session(_user())
    assert session.expires_at == _T0 + idle_ttl


# --- touch_session --------------------------------------------------------


def test_touch_session_resolves_the_owning_user_and_expands_permissions() -> None:
    clock = FixedClock(_T0)
    service, _, _, _ident, _, _ = _service(clock)
    user = _identities(service).seed("usr_1", "ada", Role.ADMIN)
    _, session = service.mint_session(user)

    identity = service.touch_session(session, tenant=_TENANT)

    assert identity is not None
    assert identity.user_id == user.user_id
    assert identity.username == "ada"
    assert identity.tenant == _TENANT
    assert identity.role is Role.ADMIN
    assert identity.hub_admin is False
    assert identity.permissions == permissions_for(Role.ADMIN, False)


def test_touch_session_resolves_a_hub_administrator_with_the_hub_permissions() -> None:
    clock = FixedClock(_T0)
    service, _, _, _ident, _, _ = _service(clock)
    user = _identities(service).seed("usr_1", "ada", Role.SUPERUSER)
    _, session = service.mint_session(user)

    identity = service.touch_session(session, tenant=_TENANT)

    assert identity is not None
    assert (identity.role, identity.hub_admin) == (Role.ADMIN, True)
    assert identity.permissions == permissions_for(Role.ADMIN, True)


def test_touch_session_resolves_no_role_for_a_user_with_no_membership() -> None:
    clock = FixedClock(_T0)
    service, _, _, _ident, _, _ = _service(clock)
    user = _identities(service).seed("usr_1", "ada", Role.PENDING)
    _, session = service.mint_session(user)

    identity = service.touch_session(session, tenant=_TENANT)

    assert identity is not None
    assert (identity.role, identity.hub_admin, identity.permissions) == (None, False, frozenset())


def test_touch_session_slides_expiry_forward() -> None:
    clock = FixedClock(_T0)
    idle_ttl = timedelta(hours=1)
    service, users, sessions, _ident, _, _ = _service(clock, idle_ttl=idle_ttl)
    user = _user()
    users.create(user)
    _, session = service.mint_session(user)
    first_expiry = session.expires_at

    clock.advance(timedelta(minutes=30))
    identity = service.touch_session(session, tenant=_TENANT)
    assert identity is not None

    slid = sessions.by_hash[session.id_hash]
    assert slid.expires_at > first_expiry
    assert slid.last_seen_at == clock.now()


def test_touch_session_skips_the_write_when_the_slide_is_under_touch_granularity() -> None:
    clock = FixedClock(_T0)
    service, users, sessions, _ident, _, _ = _service(clock, idle_ttl=timedelta(hours=1))
    user = _user()
    users.create(user)
    _, session = service.mint_session(user)

    clock.advance(timedelta(minutes=4))  # under the default 5-minute touch_granularity
    identity = service.touch_session(session, tenant=_TENANT)

    assert identity is not None
    assert sessions.touch_calls == []


def test_touch_session_writes_once_the_slide_reaches_touch_granularity() -> None:
    clock = FixedClock(_T0)
    service, users, sessions, _ident, _, _ = _service(clock, idle_ttl=timedelta(hours=1))
    user = _user()
    users.create(user)
    _, session = service.mint_session(user)

    clock.advance(timedelta(minutes=5))  # exactly the default touch_granularity
    identity = service.touch_session(session, tenant=_TENANT)

    assert identity is not None
    assert len(sessions.touch_calls) == 1
    _, _, expires_at = sessions.touch_calls[0]
    assert expires_at == clock.now() + timedelta(hours=1)


def test_touch_session_honors_a_constructor_overridden_touch_granularity() -> None:
    clock = FixedClock(_T0)
    service, users, sessions, _ident, _, _ = _service(
        clock, idle_ttl=timedelta(hours=1), touch_granularity=timedelta(minutes=1)
    )
    user = _user()
    users.create(user)
    _, session = service.mint_session(user)

    clock.advance(timedelta(minutes=1))
    identity = service.touch_session(session, tenant=_TENANT)

    assert identity is not None
    assert len(sessions.touch_calls) == 1


def test_touch_session_returns_none_once_idle_expired() -> None:
    clock = FixedClock(_T0)
    idle_ttl = timedelta(hours=1)
    service, users, _, _ident, _, _ = _service(clock, idle_ttl=idle_ttl)
    user = _user()
    users.create(user)
    _, session = service.mint_session(user)

    clock.advance(timedelta(hours=2))
    assert service.touch_session(session, tenant=_TENANT) is None


def test_touch_session_returns_none_past_absolute_max_age_even_if_recently_touched() -> None:
    """A session cannot outlive its absolute cap no matter how often it slides;
    re-fetches the freshly-slid row via ``get_by_hash`` before each call."""
    clock = FixedClock(_T0)
    service, users, sessions, _ident, _, _ = _service(
        clock, idle_ttl=timedelta(hours=1), absolute_max_age=timedelta(hours=3)
    )
    user = _user()
    users.create(user)
    _, session = service.mint_session(user)

    # Slide twice within the idle window, each time resetting last_seen — but the
    # absolute cap is measured from `created_at`, not `last_seen_at`.
    clock.advance(timedelta(minutes=50))
    assert service.touch_session(session, tenant=_TENANT) is not None
    session = sessions.get_by_hash(session.id_hash)
    assert session is not None
    clock.advance(timedelta(minutes=50))
    assert service.touch_session(session, tenant=_TENANT) is not None
    session = sessions.get_by_hash(session.id_hash)
    assert session is not None
    clock.advance(timedelta(minutes=61))  # now 3h01m past created_at
    assert service.touch_session(session, tenant=_TENANT) is None


def test_touch_session_keeps_advancing_last_seen_once_the_expiry_slide_saturates() -> None:
    """Once the idle slide's target outruns the absolute cap, ``expires_at`` stops
    moving — the touch-granularity gate must not key off that frozen delta, or
    ``last_seen_at`` would freeze with it while the session is still very much live."""
    clock = FixedClock(_T0)
    service, users, sessions, _ident, _, _ = _service(
        clock, idle_ttl=timedelta(hours=2), absolute_max_age=timedelta(hours=3)
    )
    user = _user()
    users.create(user)
    _, session = service.mint_session(user)

    clock.advance(timedelta(hours=1, minutes=30))  # past the 1h saturation threshold
    assert service.touch_session(session, tenant=_TENANT) is not None
    session = sessions.get_by_hash(session.id_hash)
    assert session is not None
    saturated_expiry = session.expires_at
    assert saturated_expiry == _T0 + timedelta(hours=3)

    clock.advance(timedelta(minutes=10))  # well past touch_granularity, expiry target unchanged
    assert service.touch_session(session, tenant=_TENANT) is not None
    resolved = sessions.get_by_hash(session.id_hash)

    assert resolved is not None
    assert resolved.expires_at == saturated_expiry
    assert resolved.last_seen_at == clock.now()


def test_touch_session_returns_none_when_the_user_no_longer_exists() -> None:
    """A session outliving its user (deleted between mint and resolve) resolves to
    nothing rather than raising."""
    clock = FixedClock(_T0)
    service, users, _, _ident, _, _ = _service(clock)
    user = _user()
    users.create(user)
    _, session = service.mint_session(user)
    del users.by_id[user.user_id]

    assert service.touch_session(session, tenant=_TENANT) is None


# --- revoke -----------------------------------------------------------------


def test_revoke_deletes_the_session_row() -> None:
    clock = FixedClock(_T0)
    service, users, sessions, _ident, _, _ = _service(clock)
    user = _user()
    users.create(user)
    _, session = service.mint_session(user)

    service.revoke(session)

    # Mirrors the edge's own read-then-resolve shape (src/blizzard/hub/api/auth_session.py).
    assert sessions.get_by_hash(session.id_hash) is None


# --- mint_username ------------------------------------------------------------


def test_mint_username_slugifies_the_handle() -> None:
    clock = FixedClock(_T0)
    service, _, _, _ident, _, _ = _service(clock)
    assert service.mint_username("Ada Lovelace") == "ada-lovelace"


def test_mint_username_appends_a_numeric_suffix_on_collision() -> None:
    clock = FixedClock(_T0)
    service, users, _, _ident, _, _ = _service(clock)
    users.create(_user(user_id="usr_1"))  # username "ada"
    assert service.mint_username("ada") == "ada-2"


def test_mint_username_suffix_climbs_past_multiple_collisions() -> None:
    clock = FixedClock(_T0)
    service, users, _, _ident, _, _ = _service(clock)
    users.create(User(user_id="usr_1", username="ada", display_name="Ada", email=None, created_at=_T0))
    users.create(User(user_id="usr_2", username="ada-2", display_name="Ada", email=None, created_at=_T0))
    assert service.mint_username("ada") == "ada-3"


def test_mint_username_falls_back_to_user_for_an_all_symbol_handle() -> None:
    clock = FixedClock(_T0)
    service, _, _, _ident, _, _ = _service(clock)
    assert service.mint_username("###") == "user"


# --- link_or_mint -------------------------------------------------


def _provider_identity(
    *, subject: str = "gh-1", handle: str = "ada", email: str | None = "ada@example.com", verified: bool = True
) -> ProviderIdentity:
    return ProviderIdentity(subject=subject, handle=handle, email=email, email_verified=verified)


def test_link_or_mint_mints_a_new_pending_user_for_an_unknown_identity() -> None:
    clock = FixedClock(_T0)
    service, users, _, identities, _, _ = _service(clock)

    user = service.link_or_mint(_provider_identity(), provider_name="github")

    assert _identities(service).memberships.facts == []
    assert user.username == "ada"
    assert user.email == "ada@example.com"
    assert users.get(user.user_id) is user or users.get(user.user_id) == user
    link = identities.get("github", "gh-1")
    assert link is not None
    assert link.user_id == user.user_id
    assert link.handle == "ada"


def test_link_or_mint_never_stores_an_unverified_email_on_a_newly_minted_user() -> None:
    """An unverified provider-reported email is not proof of ownership, so it is not
    stored on a newly minted user."""
    clock = FixedClock(_T0)
    service, _, _, _, _, _ = _service(clock)

    user = service.link_or_mint(_provider_identity(verified=False), provider_name="github")

    assert user.email is None


def test_link_or_mint_subject_mapping_wins_on_a_later_login() -> None:
    """An existing ``(provider, subject)`` always resolves its own user — never
    re-mints, never re-merges by email."""
    clock = FixedClock(_T0)
    service, _, _, _, _, _ = _service(clock)
    first = service.link_or_mint(_provider_identity(), provider_name="github")

    second = service.link_or_mint(_provider_identity(email="different@example.com"), provider_name="github")

    assert second.user_id == first.user_id


def test_link_or_mint_refreshes_the_handle_on_a_provider_side_rename() -> None:
    """A handle rename never re-mints a user — "subject mapping wins" applies to the
    handle too."""
    clock = FixedClock(_T0)
    service, _, _, identities, _, _ = _service(clock)
    first = service.link_or_mint(_provider_identity(handle="ada"), provider_name="github")

    renamed = service.link_or_mint(_provider_identity(handle="ada-lovelace"), provider_name="github")

    assert renamed.user_id == first.user_id
    link = identities.get("github", "gh-1")
    assert link is not None
    assert link.handle == "ada-lovelace"


def test_link_or_mint_attaches_a_second_provider_identity_to_the_same_verified_email() -> None:
    """Second-provider login with the same verified email lands on the same user —
    one ``users`` row, two ``identities`` rows."""
    clock = FixedClock(_T0)
    service, users, _, identities, _, _ = _service(clock)
    first = service.link_or_mint(_provider_identity(subject="gh-1", handle="ada"), provider_name="github")

    second = service.link_or_mint(
        ProviderIdentity(subject="oidc-1", handle="ada.lovelace", email="ada@example.com", email_verified=True),
        provider_name="oidc-co",
    )

    assert second.user_id == first.user_id
    assert len(users.by_id) == 1
    assert identities.list_for_user(first.user_id) == sorted(
        identities.list_for_user(first.user_id), key=lambda i: i.provider_name
    )
    assert {i.provider_name for i in identities.list_for_user(first.user_id)} == {"github", "oidc-co"}


def test_link_or_mint_never_merges_an_unverified_email_even_when_it_matches() -> None:
    """An unverified email never merges — a second (unverified) identity mints its own
    user rather than attaching to the existing one sharing that email address."""
    clock = FixedClock(_T0)
    service, users, _, _, _, _ = _service(clock)
    first = service.link_or_mint(_provider_identity(subject="gh-1"), provider_name="github")

    second = service.link_or_mint(_provider_identity(subject="oidc-1", verified=False), provider_name="oidc-co")

    assert second.user_id != first.user_id
    assert len(users.by_id) == 2


# --- state -------------------------------------------------------------


def test_start_state_then_consume_state_round_trips() -> None:
    clock = FixedClock(_T0)
    service, _, _, _, _, _ = _service(clock)

    state = service.start_state(kind=PROVIDER_LOGIN_STATE_KIND, provider_name="github", return_to="/board")
    entry = service.consume_state(state)

    assert entry is not None
    assert entry.provider_name == "github"
    assert entry.return_to == "/board"
    assert entry.kind == PROVIDER_LOGIN_STATE_KIND


def test_consume_state_is_single_use() -> None:
    clock = FixedClock(_T0)
    service, _, _, _, _, _ = _service(clock)
    state = service.start_state(kind=PROVIDER_LOGIN_STATE_KIND, provider_name="github", return_to="/")

    assert service.consume_state(state) is not None
    assert service.consume_state(state) is None


def test_consume_state_rejects_an_unknown_state() -> None:
    clock = FixedClock(_T0)
    service, _, _, _, _, _ = _service(clock)
    assert service.consume_state("never-minted") is None


def test_consume_state_rejects_an_expired_state() -> None:
    clock = FixedClock(_T0)
    service, _, _, _, _, _ = _service(clock)
    state = service.start_state(
        kind=PROVIDER_LOGIN_STATE_KIND, provider_name="github", return_to="/", ttl=timedelta(minutes=10)
    )

    clock.advance(timedelta(minutes=11))

    assert service.consume_state(state) is None


# --- assign_role ---------------------------------------------------------


def _identity(service: AuthService, user: User) -> ResolvedIdentity:
    member = service.member(user, tenant=_TENANT)
    return principal(user, tenant=_TENANT, role=member.role, hub_admin=member.hub_admin)


def _assigning(actor_role: Role, subject_role: Role) -> tuple[AuthService, User, User, AuthFactsService]:
    service, _, _, _, _, auth_facts = _service(FixedClock(_T0))
    seeded = _identities(service)
    return (
        service,
        seeded.seed("usr_actor", "actor", actor_role),
        seeded.seed("usr_subject", "subject", subject_role),
        auth_facts,
    )


def test_admin_promotes_a_guest_to_contributor() -> None:
    service, admin, guest, auth_facts = _assigning(Role.ADMIN, Role.GUEST)

    updated = service.assign_role(
        actor=_identity(service, admin), subject=service.member(guest, tenant=_TENANT), to_role=Role.CONTRIBUTOR
    )

    assert updated.role is Role.CONTRIBUTOR
    assert service.member(guest, tenant=_TENANT).role is Role.CONTRIBUTOR
    assert _identities(service).memberships.facts[-1] == MembershipFact(
        _TENANT, "usr_subject", Role.CONTRIBUTOR, _T0, "usr_actor"
    )
    facts = auth_facts.list_recent()
    assert len(facts) == 1
    assert facts[0].kind == "user_role_changed"
    assert facts[0].actor == "actor"
    assert facts[0].subject == "subject"
    assert facts[0].detail == f"guest -> contributor in {_TENANT}"


def test_assigning_pending_revokes_the_membership_naming_the_tenant() -> None:
    service, admin, guest, auth_facts = _assigning(Role.ADMIN, Role.GUEST)

    updated = service.assign_role(
        actor=_identity(service, admin), subject=service.member(guest, tenant=_TENANT), to_role=Role.PENDING
    )

    assert updated.role is None
    assert service.member(guest, tenant=_TENANT).role is None
    assert auth_facts.list_recent()[0].detail == f"guest -> none in {_TENANT}"


@pytest.mark.parametrize(
    ("actor_role", "subject_role", "to_role"),
    [
        pytest.param(Role.ADMIN, Role.GUEST, Role.ADMIN, id="admin-granting-admin"),
        pytest.param(Role.ADMIN, Role.ADMIN, Role.CONTRIBUTOR, id="admin-revoking-admin"),
        pytest.param(Role.SUPERUSER, Role.GUEST, Role.SUPERUSER, id="superuser-not-assignable"),
        pytest.param(Role.SUPERUSER, Role.SUPERUSER, Role.ADMIN, id="hub-administrator-subject"),
    ],
)
def test_the_role_change_rules_refuse(actor_role: Role, subject_role: Role, to_role: Role) -> None:
    """Granting or revoking ``admin`` takes a hub administrator; a hub administrator (``superuser``)
    is never moved through this API, in either direction, even by another."""
    service, actor, subject, auth_facts = _assigning(actor_role, subject_role)

    with pytest.raises(RoleAssignmentRefused):
        service.assign_role(
            actor=_identity(service, actor), subject=service.member(subject, tenant=_TENANT), to_role=to_role
        )
    assert auth_facts.list_recent() == []


def test_a_hub_administrator_granting_admin_succeeds() -> None:
    service, superuser, guest, _ = _assigning(Role.SUPERUSER, Role.GUEST)

    updated = service.assign_role(
        actor=_identity(service, superuser), subject=service.member(guest, tenant=_TENANT), to_role=Role.ADMIN
    )
    assert updated.role is Role.ADMIN


def test_self_role_change_is_refused() -> None:
    service, admin, _, _ = _assigning(Role.ADMIN, Role.GUEST)

    with pytest.raises(RoleAssignmentRefused):
        service.assign_role(
            actor=_identity(service, admin), subject=service.member(admin, tenant=_TENANT), to_role=Role.CONTRIBUTOR
        )


def test_assign_role_is_a_no_op_when_the_role_is_unchanged() -> None:
    service, admin, guest, auth_facts = _assigning(Role.ADMIN, Role.GUEST)
    before = list(_identities(service).memberships.facts)

    updated = service.assign_role(
        actor=_identity(service, admin), subject=service.member(guest, tenant=_TENANT), to_role=Role.GUEST
    )

    assert updated.role is Role.GUEST
    assert _identities(service).memberships.facts == before
    assert auth_facts.list_recent() == []


def test_members_reads_each_users_membership_and_hub_administrator_role() -> None:
    service, superuser, guest, _ = _assigning(Role.SUPERUSER, Role.GUEST)
    pending = _identities(service).seed("usr_pending", "pending", Role.PENDING)

    members = service.members([superuser, guest, pending], tenant=_TENANT)

    assert [(m.user.user_id, m.role, m.hub_admin) for m in members] == [
        ("usr_actor", Role.ADMIN, True),
        ("usr_subject", Role.GUEST, False),
        ("usr_pending", None, False),
    ]


# --- superuser bootstrap --------------------------------------------------


def test_record_and_get_superuser_bootstrap_round_trips() -> None:
    clock = FixedClock(_T0)
    service, _, _, _, _, _ = _service(clock)

    service.record_superuser_bootstrap(email="alice@example.com", claimed_user_id=None)
    bootstrap = service.get_superuser_bootstrap()

    assert bootstrap is not None
    assert bootstrap.email == "alice@example.com"
    assert bootstrap.claimed_user_id is None
    assert bootstrap.updated_at == _T0


@pytest.mark.parametrize("claimed_user_id", [None, "usr_alice"], ids=["unclaimed", "claimed"])
def test_cancelling_the_superuser_bootstrap_removes_only_an_unclaimed_row(claimed_user_id: str | None) -> None:
    service, _, _, _, superuser_bootstrap, _ = _service(FixedClock(_T0))
    service.record_superuser_bootstrap(email="alice@example.com", claimed_user_id=claimed_user_id)

    service.cancel_unclaimed_superuser_bootstrap()

    kept = SuperuserBootstrap(email="alice@example.com", claimed_user_id=claimed_user_id, updated_at=_T0)
    assert superuser_bootstrap.get() == (None if claimed_user_id is None else kept)


def _alice() -> ProviderIdentity:
    return _provider_identity(subject="gh-alice", handle="alice", email="alice@example.com", verified=True)


def test_the_first_claim_makes_a_hub_administrator_and_the_first_tenants_admin() -> None:
    """With one tenant and no admin in it, the claim grants the hub administrator role and an
    ``admin`` membership, both ``bootstrap``, recording the membership's ``user_role_changed``."""
    clock = FixedClock(_T0)
    service, _, _, _, superuser_bootstrap, auth_facts = _service(clock)
    service.record_superuser_bootstrap(email="alice@example.com", claimed_user_id=None)

    user = service.link_or_mint(_alice(), provider_name="github")

    seeded = _identities(service)
    assert seeded.hub_admins.facts == [HubAdminFact(user.user_id, True, _T0, "bootstrap")]
    assert seeded.memberships.facts == [MembershipFact(_TENANT, user.user_id, Role.ADMIN, _T0, "bootstrap")]
    bootstrap = superuser_bootstrap.get()
    assert bootstrap is not None
    assert bootstrap.claimed_user_id == user.user_id
    [fact] = auth_facts.list_recent()
    assert (fact.kind, fact.actor, fact.detail) == ("user_role_changed", "system", f"none -> admin in {_TENANT}")


@pytest.mark.parametrize("second_tenant", [False, True], ids=["tenant-has-an-admin", "two-tenants"])
def test_the_claim_grants_no_membership_once_a_tenant_has_an_admin_or_a_second_exists(second_tenant: bool) -> None:
    clock = FixedClock(_T0)
    service, _, _, _, _, _ = _service(clock)
    seeded = _identities(service)
    if second_tenant:
        seeded.memberships.tenants = [_TENANT, TenantId("ten_two")]
    else:
        seeded.seed("usr_admin", "admin", Role.ADMIN)
    before = list(seeded.memberships.facts)
    service.record_superuser_bootstrap(email="alice@example.com", claimed_user_id=None)

    user = service.link_or_mint(_alice(), provider_name="github")

    assert seeded.hub_admins.holders() == [user.user_id]
    assert seeded.memberships.facts == before


def test_a_returning_user_claims_a_newly_configured_target_at_sign_in() -> None:
    """A user who already exists claims the target when they sign in, keeping their membership."""
    clock = FixedClock(_T0)
    service, _, _, identities, superuser_bootstrap, _ = _service(clock)
    seeded = _identities(service)
    seeded.seed("usr_admin", "admin", Role.ADMIN)
    alice = seeded.seed("usr_alice", "alice", Role.GUEST, email="alice@example.com")
    identities.link(Identity("github", "gh-alice", alice.user_id, "alice", _T0))
    service.record_superuser_bootstrap(email="alice@example.com", claimed_user_id=None)

    user = service.link_or_mint(_alice(), provider_name="github")

    assert user == alice
    assert seeded.hub_admins.holders() == [alice.user_id]
    assert service.member(alice, tenant=_TENANT).role is Role.GUEST
    bootstrap = superuser_bootstrap.get()
    assert bootstrap is not None
    assert bootstrap.claimed_user_id == alice.user_id


def test_link_or_mint_does_not_claim_a_bootstrap_target_for_a_different_email() -> None:
    clock = FixedClock(_T0)
    service, _, _, _, superuser_bootstrap, _ = _service(clock)
    service.record_superuser_bootstrap(email="alice@example.com", claimed_user_id=None)

    service.link_or_mint(_provider_identity(email="bob@example.com"), provider_name="github")

    assert _identities(service).hub_admins.facts == []
    bootstrap = superuser_bootstrap.get()
    assert bootstrap is not None
    assert bootstrap.claimed_user_id is None


def test_link_or_mint_does_not_reclaim_an_already_claimed_bootstrap_target() -> None:
    clock = FixedClock(_T0)
    service, _, _, _, superuser_bootstrap, _ = _service(clock)
    service.record_superuser_bootstrap(email="alice@example.com", claimed_user_id="usr_already")

    service.link_or_mint(_provider_identity(subject="gh-alice-2", email="alice@example.com"), provider_name="github")

    assert _identities(service).hub_admins.facts == []
    assert _identities(service).memberships.facts == []
    bootstrap = superuser_bootstrap.get()
    assert bootstrap is not None
    assert bootstrap.claimed_user_id == "usr_already"
