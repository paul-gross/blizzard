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
from blizzard.hub.auth.hashing import hash_session_id
from blizzard.hub.auth.models import Identity, Session, User
from blizzard.hub.auth.service import AuthService

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

    def create(self, user: User) -> None:
        self.by_id[user.user_id] = user


class _FakeIdentityRepository:
    def __init__(self) -> None:
        self.rows: list[Identity] = []

    def get(self, provider_name: str, subject: str) -> Identity | None:
        return next((i for i in self.rows if i.provider_name == provider_name and i.subject == subject), None)

    def list_for_user(self, user_id: str) -> list[Identity]:
        return [i for i in self.rows if i.user_id == user_id]

    def link(self, identity: Identity) -> None:
        self.rows.append(identity)


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


def _service(clock: FixedClock, **kwargs: object) -> tuple[AuthService, _FakeUserRepository, _FakeSessionRepository]:
    users = _FakeUserRepository()
    identities = _FakeIdentityRepository()
    sessions = _FakeSessionRepository()
    service = AuthService(users=users, identities=identities, sessions=sessions, clock=clock, **kwargs)  # type: ignore[arg-type]
    return service, users, sessions


def _user(user_id: str = "usr_1", *, role: Role = Role.CONTRIBUTOR) -> User:
    return User(user_id=user_id, username="ada", display_name="Ada", email=None, role=role, created_at=_T0)


# --- mint_session -------------------------------------------------------------


def test_mint_session_stores_only_the_hash_never_the_plaintext() -> None:
    clock = FixedClock(_T0)
    service, _, sessions = _service(clock)
    user = _user()

    plaintext, session = service.mint_session(user)

    assert session.id_hash == hash_session_id(plaintext)
    assert session.id_hash != plaintext
    assert sessions.by_hash[session.id_hash] is session


def test_mint_session_sets_expiry_from_idle_ttl() -> None:
    clock = FixedClock(_T0)
    idle_ttl = timedelta(hours=2)
    service, _, _ = _service(clock, idle_ttl=idle_ttl)
    _, session = service.mint_session(_user())
    assert session.expires_at == _T0 + idle_ttl


# --- touch_session --------------------------------------------------------


def test_touch_session_resolves_the_owning_user_and_expands_permissions() -> None:
    clock = FixedClock(_T0)
    service, users, _ = _service(clock)
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
    service, users, sessions = _service(clock, idle_ttl=idle_ttl)
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
    service, users, _ = _service(clock, idle_ttl=idle_ttl)
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
    service, users, sessions = _service(clock, idle_ttl=timedelta(hours=1), absolute_max_age=timedelta(hours=3))
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
    service, users, _ = _service(clock)
    user = _user()
    users.create(user)
    _, session = service.mint_session(user)
    del users.by_id[user.user_id]

    assert service.touch_session(session) is None


# --- revoke -----------------------------------------------------------------


def test_revoke_deletes_the_session_row() -> None:
    clock = FixedClock(_T0)
    service, users, sessions = _service(clock)
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
    service, _, _ = _service(clock)
    assert service.mint_username("Ada Lovelace") == "ada-lovelace"


def test_mint_username_appends_a_numeric_suffix_on_collision() -> None:
    clock = FixedClock(_T0)
    service, users, _ = _service(clock)
    users.create(_user(user_id="usr_1"))  # username "ada"
    assert service.mint_username("ada") == "ada-2"


def test_mint_username_suffix_climbs_past_multiple_collisions() -> None:
    clock = FixedClock(_T0)
    service, users, _ = _service(clock)
    users.create(User(user_id="usr_1", username="ada", display_name="Ada", email=None, role=Role.GUEST, created_at=_T0))
    users.create(
        User(user_id="usr_2", username="ada-2", display_name="Ada", email=None, role=Role.GUEST, created_at=_T0)
    )
    assert service.mint_username("ada") == "ada-3"


def test_mint_username_falls_back_to_user_for_an_all_symbol_handle() -> None:
    clock = FixedClock(_T0)
    service, _, _ = _service(clock)
    assert service.mint_username("###") == "user"
