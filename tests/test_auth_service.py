"""``AuthService`` — mint/resolve/slide sessions, mint collision-free usernames (unit
tier, issue #91).

Exercised against in-memory fakes of the three repository Protocols — pure domain
logic in isolation, mirroring ``tests/test_hub_auth.py``'s own unit-tier shape for
``assert_owns``. The real SQLAlchemy adapters are exercised at component tier in
``tests/test_auth_repositories.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.auth_core import Role, expand
from blizzard.foundation.clock import FixedClock
from blizzard.hub.auth.facts import AuthFactsService
from blizzard.hub.auth.hashing import hash_session_id
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
from blizzard.hub.auth.service import PROVIDER_LOGIN_STATE_KIND, AuthService, RoleAssignmentRefused

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


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

    def list_all(self) -> list[User]:
        return sorted(self.by_id.values(), key=lambda u: u.created_at)

    def create(self, user: User) -> None:
        self.by_id[user.user_id] = user

    def update_role(self, user_id: str, role: Role) -> None:
        user = self.by_id[user_id]
        self.by_id[user_id] = User(
            user_id=user.user_id,
            username=user.username,
            display_name=user.display_name,
            email=user.email,
            role=role,
            created_at=user.created_at,
        )


class _FakeIdentityRepository:
    def __init__(self) -> None:
        self.rows: list[Identity] = []

    def get(self, provider_name: str, subject: str) -> Identity | None:
        return next((i for i in self.rows if i.provider_name == provider_name and i.subject == subject), None)

    def list_for_user(self, user_id: str) -> list[Identity]:
        return [i for i in self.rows if i.user_id == user_id]

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

    def get_by_hash(self, id_hash: str) -> Session | None:
        return self.by_hash.get(id_hash)

    def create(self, session: Session) -> None:
        self.by_hash[session.id_hash] = session

    def touch(self, id_hash: str, *, last_seen_at: datetime, expires_at: datetime) -> None:
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
    auth_facts = AuthFactsService(facts=_FakeAuthFactsRepository(), clock=clock)
    service = AuthService(
        users=users,
        identities=identities,
        sessions=sessions,
        auth_state=auth_state,
        clock=clock,
        superuser_bootstrap=superuser_bootstrap,
        auth_facts=auth_facts,
        **kwargs,  # type: ignore[arg-type]
    )
    return service, users, sessions, identities, superuser_bootstrap, auth_facts


def _user(user_id: str = "usr_1", *, role: Role = Role.CONTRIBUTOR) -> User:
    return User(user_id=user_id, username="ada", display_name="Ada", email=None, role=role, created_at=_T0)


# --- mint_session -------------------------------------------------------------


def test_mint_session_stores_only_the_hash_never_the_plaintext() -> None:
    clock = FixedClock(_T0)
    service, _, sessions, _ident, _, _ = _service(clock)
    user = _user()

    plaintext, session = service.mint_session(user)

    assert session.id_hash == hash_session_id(plaintext)
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
    service, users, _, _ident, _, _ = _service(clock)
    user = _user(role=Role.ADMIN)
    users.create(user)
    _, session = service.mint_session(user)

    identity = service.touch_session(session)

    assert identity is not None
    assert identity.user_id == user.user_id
    assert identity.username == "ada"
    assert identity.display_name == "Ada"
    assert identity.role is Role.ADMIN
    assert identity.permissions == expand(Role.ADMIN)


def test_touch_session_slides_expiry_forward() -> None:
    clock = FixedClock(_T0)
    idle_ttl = timedelta(hours=1)
    service, users, sessions, _ident, _, _ = _service(clock, idle_ttl=idle_ttl)
    user = _user()
    users.create(user)
    _, session = service.mint_session(user)
    first_expiry = session.expires_at

    clock.advance(timedelta(minutes=30))
    identity = service.touch_session(session)
    assert identity is not None

    slid = sessions.by_hash[session.id_hash]
    assert slid.expires_at > first_expiry
    assert slid.last_seen_at == clock.now()


def test_touch_session_returns_none_once_idle_expired() -> None:
    clock = FixedClock(_T0)
    idle_ttl = timedelta(hours=1)
    service, users, _, _ident, _, _ = _service(clock, idle_ttl=idle_ttl)
    user = _user()
    users.create(user)
    _, session = service.mint_session(user)

    clock.advance(timedelta(hours=2))
    assert service.touch_session(session) is None


def test_touch_session_returns_none_past_absolute_max_age_even_if_recently_touched() -> None:
    """A session cannot outlive its absolute cap no matter how often it slides.

    Mirrors the edge's own pattern (``hub/api/auth_session.py``): each call re-fetches
    the freshly-slid row via ``get_by_hash`` before handing it to ``touch_session``,
    exactly as a fresh request re-resolves the session from the store — a stale,
    in-hand ``Session`` object is not what production ever passes twice."""
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
    assert service.touch_session(session) is not None
    session = sessions.get_by_hash(session.id_hash)
    assert session is not None
    clock.advance(timedelta(minutes=50))
    assert service.touch_session(session) is not None
    session = sessions.get_by_hash(session.id_hash)
    assert session is not None
    clock.advance(timedelta(minutes=61))  # now 3h01m past created_at
    assert service.touch_session(session) is None


def test_touch_session_returns_none_when_the_user_no_longer_exists() -> None:
    """A session outliving its user (deleted between mint and resolve) resolves to
    nothing rather than raising — the edge treats this exactly like "no session"."""
    clock = FixedClock(_T0)
    service, users, _, _ident, _, _ = _service(clock)
    user = _user()
    users.create(user)
    _, session = service.mint_session(user)
    del users.by_id[user.user_id]

    assert service.touch_session(session) is None


# --- revoke -----------------------------------------------------------------


def test_revoke_deletes_the_session_row() -> None:
    clock = FixedClock(_T0)
    service, users, sessions, _ident, _, _ = _service(clock)
    user = _user()
    users.create(user)
    _, session = service.mint_session(user)

    service.revoke(session)

    # Mirrors the edge's own read-then-resolve shape: a revoked session no longer
    # resolves via the read repo at all, so a fresh request's `get_by_hash` (what
    # `resolve_identity` actually calls) sees nothing — the edge never reaches
    # `touch_session` on a deleted row.
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
    users.create(User(user_id="usr_1", username="ada", display_name="Ada", email=None, role=Role.GUEST, created_at=_T0))
    users.create(
        User(user_id="usr_2", username="ada-2", display_name="Ada", email=None, role=Role.GUEST, created_at=_T0)
    )
    assert service.mint_username("ada") == "ada-3"


def test_mint_username_falls_back_to_user_for_an_all_symbol_handle() -> None:
    clock = FixedClock(_T0)
    service, _, _, _ident, _, _ = _service(clock)
    assert service.mint_username("###") == "user"


# --- link_or_mint (issue #92) -------------------------------------------------


def _provider_identity(
    *, subject: str = "gh-1", handle: str = "ada", email: str | None = "ada@example.com", verified: bool = True
) -> ProviderIdentity:
    return ProviderIdentity(subject=subject, handle=handle, email=email, email_verified=verified)


def test_link_or_mint_mints_a_new_guest_user_for_an_unknown_identity() -> None:
    clock = FixedClock(_T0)
    service, users, _, identities, _, _ = _service(clock)

    user = service.link_or_mint(_provider_identity(), provider_name="github")

    assert user.role == Role.GUEST
    assert user.username == "ada"
    assert user.email == "ada@example.com"
    assert users.get(user.user_id) is user or users.get(user.user_id) == user
    link = identities.get("github", "gh-1")
    assert link is not None
    assert link.user_id == user.user_id
    assert link.handle == "ada"


def test_link_or_mint_never_stores_an_unverified_email_on_a_newly_minted_user() -> None:
    """An unverified provider-reported email is not proof of ownership — storing it
    would let a later *verified* login for the same address merge into whatever
    account an attacker seeded with an unverified claim."""
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


# --- state (decision D5) -------------------------------------------------------


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


# --- assign_role (issue #94) ---------------------------------------------------


def _identity(user: User) -> ResolvedIdentity:
    return ResolvedIdentity(
        user_id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        permissions=expand(user.role),
    )


def test_admin_promotes_a_guest_to_contributor() -> None:
    clock = FixedClock(_T0)
    service, users, _, _, _, auth_facts = _service(clock)
    admin = User(
        user_id="usr_admin", username="admin", display_name="Admin", email=None, role=Role.ADMIN, created_at=_T0
    )
    guest = User(
        user_id="usr_guest", username="guest", display_name="Guest", email=None, role=Role.GUEST, created_at=_T0
    )
    users.create(admin)
    users.create(guest)

    updated = service.assign_role(actor=_identity(admin), subject=guest, to_role=Role.CONTRIBUTOR)

    assert updated.role is Role.CONTRIBUTOR
    stored_guest = users.get("usr_guest")
    assert stored_guest is not None
    assert stored_guest.role is Role.CONTRIBUTOR
    facts = auth_facts.list_recent()
    assert len(facts) == 1
    assert facts[0].kind == "user_role_changed"
    assert facts[0].actor == "admin"
    assert facts[0].subject == "guest"
    assert facts[0].detail == "guest -> contributor"


def test_admin_granting_admin_is_refused() -> None:
    clock = FixedClock(_T0)
    service, users, _, _, _, _ = _service(clock)
    admin = User(
        user_id="usr_admin", username="admin", display_name="Admin", email=None, role=Role.ADMIN, created_at=_T0
    )
    guest = User(
        user_id="usr_guest", username="guest", display_name="Guest", email=None, role=Role.GUEST, created_at=_T0
    )
    users.create(admin)
    users.create(guest)

    with pytest.raises(RoleAssignmentRefused):
        service.assign_role(actor=_identity(admin), subject=guest, to_role=Role.ADMIN)


def test_superuser_granting_admin_succeeds() -> None:
    clock = FixedClock(_T0)
    service, users, _, _, _, _ = _service(clock)
    superuser = User(
        user_id="usr_su", username="su", display_name="Su", email=None, role=Role.SUPERUSER, created_at=_T0
    )
    guest = User(
        user_id="usr_guest", username="guest", display_name="Guest", email=None, role=Role.GUEST, created_at=_T0
    )
    users.create(superuser)
    users.create(guest)

    updated = service.assign_role(actor=_identity(superuser), subject=guest, to_role=Role.ADMIN)
    assert updated.role is Role.ADMIN


def test_admin_revoking_admin_is_refused() -> None:
    clock = FixedClock(_T0)
    service, users, _, _, _, _ = _service(clock)
    admin_actor = User(
        user_id="usr_admin1", username="admin1", display_name="A1", email=None, role=Role.ADMIN, created_at=_T0
    )
    other_admin = User(
        user_id="usr_admin2", username="admin2", display_name="A2", email=None, role=Role.ADMIN, created_at=_T0
    )
    users.create(admin_actor)
    users.create(other_admin)

    with pytest.raises(RoleAssignmentRefused):
        service.assign_role(actor=_identity(admin_actor), subject=other_admin, to_role=Role.CONTRIBUTOR)


def test_self_role_change_is_refused() -> None:
    clock = FixedClock(_T0)
    service, users, _, _, _, _ = _service(clock)
    admin = User(
        user_id="usr_admin", username="admin", display_name="Admin", email=None, role=Role.ADMIN, created_at=_T0
    )
    users.create(admin)

    with pytest.raises(RoleAssignmentRefused):
        service.assign_role(actor=_identity(admin), subject=admin, to_role=Role.CONTRIBUTOR)


def test_superuser_is_not_assignable_through_the_api() -> None:
    clock = FixedClock(_T0)
    service, users, _, _, _, _ = _service(clock)
    superuser = User(
        user_id="usr_su", username="su", display_name="Su", email=None, role=Role.SUPERUSER, created_at=_T0
    )
    guest = User(
        user_id="usr_guest", username="guest", display_name="Guest", email=None, role=Role.GUEST, created_at=_T0
    )
    users.create(superuser)
    users.create(guest)

    with pytest.raises(RoleAssignmentRefused):
        service.assign_role(actor=_identity(superuser), subject=guest, to_role=Role.SUPERUSER)


def test_moving_a_stored_superuser_subject_is_refused() -> None:
    """``superuser`` is bootstrap-only in *both* directions — a stored ``superuser``
    subject cannot be demoted through this API either, even by another ``superuser``."""
    clock = FixedClock(_T0)
    service, users, _, _, _, _ = _service(clock)
    actor = User(user_id="usr_su1", username="su1", display_name="Su1", email=None, role=Role.SUPERUSER, created_at=_T0)
    subject = User(
        user_id="usr_su2", username="su2", display_name="Su2", email=None, role=Role.SUPERUSER, created_at=_T0
    )
    users.create(actor)
    users.create(subject)

    with pytest.raises(RoleAssignmentRefused):
        service.assign_role(actor=_identity(actor), subject=subject, to_role=Role.ADMIN)


def test_assign_role_is_a_no_op_when_the_role_is_unchanged() -> None:
    clock = FixedClock(_T0)
    service, users, _, _, _, auth_facts = _service(clock)
    admin = User(
        user_id="usr_admin", username="admin", display_name="Admin", email=None, role=Role.ADMIN, created_at=_T0
    )
    guest = User(
        user_id="usr_guest", username="guest", display_name="Guest", email=None, role=Role.GUEST, created_at=_T0
    )
    users.create(admin)
    users.create(guest)

    updated = service.assign_role(actor=_identity(admin), subject=guest, to_role=Role.GUEST)

    assert updated.role is Role.GUEST
    assert auth_facts.list_recent() == []


# --- superuser bootstrap (issue #94) --------------------------------------------


def test_bootstrap_apply_role_records_a_system_actor_fact() -> None:
    clock = FixedClock(_T0)
    service, users, _, _, _, auth_facts = _service(clock)
    admin = User(user_id="usr_1", username="ada", display_name="Ada", email=None, role=Role.ADMIN, created_at=_T0)
    users.create(admin)

    updated = service.bootstrap_apply_role(admin, Role.SUPERUSER)

    assert updated.role is Role.SUPERUSER
    stored_admin = users.get("usr_1")
    assert stored_admin is not None
    assert stored_admin.role is Role.SUPERUSER
    facts = auth_facts.list_recent()
    assert len(facts) == 1
    assert facts[0].actor == "system"
    assert facts[0].detail == "admin -> superuser"


def test_record_and_get_superuser_bootstrap_round_trips() -> None:
    clock = FixedClock(_T0)
    service, _, _, _, _, _ = _service(clock)

    service.record_superuser_bootstrap(email="alice@example.com", claimed_user_id=None)
    bootstrap = service.get_superuser_bootstrap()

    assert bootstrap is not None
    assert bootstrap.email == "alice@example.com"
    assert bootstrap.claimed_user_id is None
    assert bootstrap.updated_at == _T0


def test_clear_superuser_bootstrap_deletes_the_row() -> None:
    clock = FixedClock(_T0)
    service, _, _, _, _, _ = _service(clock)
    service.record_superuser_bootstrap(email="alice@example.com", claimed_user_id=None)

    service.clear_superuser_bootstrap()

    assert service.get_superuser_bootstrap() is None


def test_link_or_mint_claims_an_unclaimed_bootstrap_target_on_first_verified_login() -> None:
    """The bootstrap row's own singleton claim, run inside ``link_or_mint``'s
    newly-minted-user branch — the only branch a pre-provisioned, still-unclaimed
    target can first resolve through."""
    clock = FixedClock(_T0)
    service, users, _, _, superuser_bootstrap, auth_facts = _service(clock)
    service.record_superuser_bootstrap(email="alice@example.com", claimed_user_id=None)

    user = service.link_or_mint(
        _provider_identity(subject="gh-alice", handle="alice", email="alice@example.com", verified=True),
        provider_name="github",
    )

    assert user.role is Role.SUPERUSER
    stored_user = users.get(user.user_id)
    assert stored_user is not None
    assert stored_user.role is Role.SUPERUSER
    bootstrap = superuser_bootstrap.get()
    assert bootstrap is not None
    assert bootstrap.claimed_user_id == user.user_id
    kinds = [f.kind for f in auth_facts.list_recent()]
    assert "user_role_changed" in kinds


def test_link_or_mint_does_not_claim_a_bootstrap_target_for_a_different_email() -> None:
    clock = FixedClock(_T0)
    service, _, _, _, superuser_bootstrap, _ = _service(clock)
    service.record_superuser_bootstrap(email="alice@example.com", claimed_user_id=None)

    user = service.link_or_mint(_provider_identity(email="bob@example.com"), provider_name="github")

    assert user.role is Role.GUEST
    bootstrap = superuser_bootstrap.get()
    assert bootstrap is not None
    assert bootstrap.claimed_user_id is None


def test_link_or_mint_does_not_reclaim_an_already_claimed_bootstrap_target() -> None:
    clock = FixedClock(_T0)
    service, _, _, _, superuser_bootstrap, _ = _service(clock)
    service.record_superuser_bootstrap(email="alice@example.com", claimed_user_id="usr_already")

    user = service.link_or_mint(
        _provider_identity(subject="gh-alice-2", email="alice@example.com"), provider_name="github"
    )

    assert user.role is Role.GUEST
    bootstrap = superuser_bootstrap.get()
    assert bootstrap is not None
    assert bootstrap.claimed_user_id == "usr_already"
