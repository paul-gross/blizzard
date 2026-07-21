"""The identity-spine SQLAlchemy adapters — real store, injected errors (component
tier, issue #91).

Mirrors ``tests/test_runner_enrollment.py``'s own real-store shape for the fleet
registry: each adapter is exercised over a migrated sqlite engine, with the injected
:class:`~blizzard.hub.auth.errors.RepoErrorFactory` proven to wrap a raced/unexpected
``IntegrityError`` into the domain :class:`~blizzard.hub.auth.errors.RepoError`.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
import structlog

from blizzard.auth_core import Role
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.auth.errors import RepoError, RepoErrorFactory
from blizzard.hub.auth.internal.auth_facts_repository import AuthFactsRepository
from blizzard.hub.auth.internal.auth_state_repository import AuthStateRepository
from blizzard.hub.auth.internal.identity_repository import IdentityRepository
from blizzard.hub.auth.internal.session_repository import SessionRepository
from blizzard.hub.auth.internal.superuser_bootstrap_repository import SuperuserBootstrapRepository
from blizzard.hub.auth.internal.user_repository import UserRepository
from blizzard.hub.auth.models import AuthFact, AuthStateEntry, Identity, Session, SuperuserBootstrap, User
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path: Path):  # type: ignore[no-untyped-def]
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    return create_engine_from_url(db_url)


@pytest.fixture
def errors() -> RepoErrorFactory:
    return RepoErrorFactory(structlog.get_logger("test"))


# --- UserRepository -----------------------------------------------------------


def test_user_create_and_get_round_trip(engine, errors: RepoErrorFactory) -> None:  # type: ignore[no-untyped-def]
    repo = UserRepository(engine, errors)
    user = User(
        user_id="usr_1", username="ada", display_name="Ada", email="ada@example.com", role=Role.ADMIN, created_at=_T0
    )
    repo.create(user)

    fetched = repo.get("usr_1")
    assert fetched == user
    assert repo.get_by_username("ada") == user
    assert repo.get_by_email("ada@example.com") == user
    assert repo.username_exists("ada") is True
    assert repo.username_exists("nobody") is False
    assert repo.get("usr_missing") is None


def test_user_create_rejects_a_duplicate_username(engine, errors: RepoErrorFactory) -> None:  # type: ignore[no-untyped-def]
    repo = UserRepository(engine, errors)
    repo.create(User(user_id="usr_1", username="ada", display_name="Ada", email=None, role=Role.GUEST, created_at=_T0))

    with pytest.raises(RepoError):
        repo.create(
            User(user_id="usr_2", username="ada", display_name="Ada Two", email=None, role=Role.GUEST, created_at=_T0)
        )


def test_user_create_rejects_a_duplicate_email(engine, errors: RepoErrorFactory) -> None:  # type: ignore[no-untyped-def]
    repo = UserRepository(engine, errors)
    repo.create(
        User(user_id="usr_1", username="a", display_name="A", email="dup@example.com", role=Role.GUEST, created_at=_T0)
    )

    with pytest.raises(RepoError):
        repo.create(
            User(
                user_id="usr_2",
                username="b",
                display_name="B",
                email="dup@example.com",
                role=Role.GUEST,
                created_at=_T0,
            )
        )


def test_user_create_allows_multiple_null_emails(engine, errors: RepoErrorFactory) -> None:  # type: ignore[no-untyped-def]
    repo = UserRepository(engine, errors)
    repo.create(User(user_id="usr_1", username="a", display_name="A", email=None, role=Role.GUEST, created_at=_T0))
    repo.create(
        User(user_id="usr_2", username="b", display_name="B", email=None, role=Role.GUEST, created_at=_T0)
    )  # no raise


def test_user_list_all_returns_every_row_oldest_first(engine, errors: RepoErrorFactory) -> None:  # type: ignore[no-untyped-def]
    repo = UserRepository(engine, errors)
    later = datetime(2026, 1, 2, tzinfo=UTC)
    repo.create(User(user_id="usr_2", username="b", display_name="B", email=None, role=Role.GUEST, created_at=later))
    repo.create(User(user_id="usr_1", username="a", display_name="A", email=None, role=Role.GUEST, created_at=_T0))

    assert [u.user_id for u in repo.list_all()] == ["usr_1", "usr_2"]


def test_user_update_role_writes_in_place(engine, errors: RepoErrorFactory) -> None:  # type: ignore[no-untyped-def]
    repo = UserRepository(engine, errors)
    repo.create(User(user_id="usr_1", username="a", display_name="A", email=None, role=Role.GUEST, created_at=_T0))

    repo.update_role("usr_1", Role.ADMIN)

    updated = repo.get("usr_1")
    assert updated is not None
    assert updated.role == Role.ADMIN


# --- IdentityRepository --------------------------------------------------------


def test_identity_link_and_get_round_trip(engine, errors: RepoErrorFactory) -> None:  # type: ignore[no-untyped-def]
    users = UserRepository(engine, errors)
    users.create(User(user_id="usr_1", username="a", display_name="A", email=None, role=Role.GUEST, created_at=_T0))
    identities = IdentityRepository(engine, errors)
    identity = Identity(provider_name="github", subject="123", user_id="usr_1", handle="ada", created_at=_T0)
    identities.link(identity)

    assert identities.get("github", "123") == identity
    assert identities.get("github", "missing") is None
    assert identities.list_for_user("usr_1") == [identity]


def test_identity_link_rejects_a_duplicate_provider_subject_pair(engine, errors: RepoErrorFactory) -> None:  # type: ignore[no-untyped-def]
    users = UserRepository(engine, errors)
    users.create(User(user_id="usr_1", username="a", display_name="A", email=None, role=Role.GUEST, created_at=_T0))
    identities = IdentityRepository(engine, errors)
    identities.link(Identity(provider_name="github", subject="123", user_id="usr_1", handle="ada", created_at=_T0))

    with pytest.raises(RepoError):
        identities.link(
            Identity(provider_name="github", subject="123", user_id="usr_1", handle="renamed", created_at=_T0)
        )


def test_identity_update_handle_refreshes_in_place(engine, errors: RepoErrorFactory) -> None:  # type: ignore[no-untyped-def]
    users = UserRepository(engine, errors)
    users.create(User(user_id="usr_1", username="a", display_name="A", email=None, role=Role.GUEST, created_at=_T0))
    identities = IdentityRepository(engine, errors)
    identities.link(Identity(provider_name="github", subject="123", user_id="usr_1", handle="ada", created_at=_T0))

    identities.update_handle("github", "123", handle="ada-lovelace")

    refreshed = identities.get("github", "123")
    assert refreshed is not None
    assert refreshed.handle == "ada-lovelace"
    assert refreshed.created_at == _T0  # untouched


def test_identity_distinct_provider_names(engine, errors: RepoErrorFactory) -> None:  # type: ignore[no-untyped-def]
    users = UserRepository(engine, errors)
    users.create(User(user_id="usr_1", username="a", display_name="A", email=None, role=Role.GUEST, created_at=_T0))
    identities = IdentityRepository(engine, errors)
    assert identities.distinct_provider_names() == set()

    identities.link(Identity(provider_name="github", subject="1", user_id="usr_1", handle="a", created_at=_T0))
    identities.link(Identity(provider_name="oidc-co", subject="2", user_id="usr_1", handle="a", created_at=_T0))
    assert identities.distinct_provider_names() == {"github", "oidc-co"}


# --- SessionRepository ----------------------------------------------------


def test_session_create_get_touch_delete_round_trip(engine, errors: RepoErrorFactory) -> None:  # type: ignore[no-untyped-def]
    users = UserRepository(engine, errors)
    users.create(User(user_id="usr_1", username="a", display_name="A", email=None, role=Role.GUEST, created_at=_T0))
    sessions = SessionRepository(engine, errors)
    session = Session(id_hash="hash1", user_id="usr_1", created_at=_T0, expires_at=_T0, last_seen_at=_T0)
    sessions.create(session)

    assert sessions.get_by_hash("hash1") == session
    assert sessions.get_by_hash("missing") is None

    later = datetime(2026, 1, 2, tzinfo=UTC)
    sessions.touch("hash1", last_seen_at=later, expires_at=later)
    touched = sessions.get_by_hash("hash1")
    assert touched is not None
    assert touched.last_seen_at == later
    assert touched.expires_at == later
    assert touched.created_at == _T0  # untouched

    sessions.delete("hash1")
    assert sessions.get_by_hash("hash1") is None


def test_session_create_rejects_a_duplicate_id_hash(engine, errors: RepoErrorFactory) -> None:  # type: ignore[no-untyped-def]
    users = UserRepository(engine, errors)
    users.create(User(user_id="usr_1", username="a", display_name="A", email=None, role=Role.GUEST, created_at=_T0))
    sessions = SessionRepository(engine, errors)
    sessions.create(Session(id_hash="hash1", user_id="usr_1", created_at=_T0, expires_at=_T0, last_seen_at=_T0))

    with pytest.raises(RepoError):
        sessions.create(Session(id_hash="hash1", user_id="usr_1", created_at=_T0, expires_at=_T0, last_seen_at=_T0))


# --- AuthStateRepository (issue #92) -------------------------------------------


def test_auth_state_create_get_consume_round_trip(engine, errors: RepoErrorFactory) -> None:  # type: ignore[no-untyped-def]
    repo = AuthStateRepository(engine, errors)
    entry = AuthStateEntry(
        state="st1",
        kind="provider_login",
        provider_name="github",
        return_to="/board",
        code_challenge=None,
        created_at=_T0,
        expires_at=_T0,
    )
    repo.create(entry)

    assert repo.get("st1") == entry
    assert repo.get("missing") is None

    consumed = repo.consume("st1")
    assert consumed == entry
    assert repo.get("st1") is None  # single-use: deleted on consume


def test_auth_state_consume_is_idempotently_none_the_second_time(engine, errors: RepoErrorFactory) -> None:  # type: ignore[no-untyped-def]
    repo = AuthStateRepository(engine, errors)
    repo.create(
        AuthStateEntry(
            state="st1",
            kind="provider_login",
            provider_name="github",
            return_to="/",
            code_challenge=None,
            created_at=_T0,
            expires_at=_T0,
        )
    )
    assert repo.consume("st1") is not None
    assert repo.consume("st1") is None


def test_auth_state_consume_is_single_use_under_concurrent_callers(engine, errors: RepoErrorFactory) -> None:  # type: ignore[no-untyped-def]
    """Two racing ``consume`` calls on the same state must not both win (issue #92
    pre-push fix): the DELETE is the gate, not the preceding SELECT."""
    repo = AuthStateRepository(engine, errors)
    repo.create(
        AuthStateEntry(
            state="st1",
            kind="provider_login",
            provider_name="github",
            return_to="/",
            code_challenge=None,
            created_at=_T0,
            expires_at=_T0,
        )
    )

    barrier = threading.Barrier(2)
    results: list[AuthStateEntry | None] = [None, None]

    def race(index: int) -> None:
        barrier.wait()
        results[index] = repo.consume("st1")

    threads = [threading.Thread(target=race, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [r for r in results if r is not None]
    losers = [r for r in results if r is None]
    assert len(winners) == 1
    assert len(losers) == 1
    assert repo.get("st1") is None


def test_auth_state_create_rejects_a_duplicate_state(engine, errors: RepoErrorFactory) -> None:  # type: ignore[no-untyped-def]
    repo = AuthStateRepository(engine, errors)
    entry = AuthStateEntry(
        state="st1",
        kind="provider_login",
        provider_name="github",
        return_to="/",
        code_challenge=None,
        created_at=_T0,
        expires_at=_T0,
    )
    repo.create(entry)
    with pytest.raises(RepoError):
        repo.create(entry)


# --- AuthFactsRepository (issue #92) -------------------------------------------


def test_auth_facts_create_and_list_recent_newest_first(engine) -> None:  # type: ignore[no-untyped-def]
    repo = AuthFactsRepository(engine)
    repo.create(AuthFact(kind="login_failed", actor="1.2.3.4", subject="github", detail="bad state", recorded_at=_T0))
    later = datetime(2026, 1, 2, tzinfo=UTC)
    repo.create(AuthFact(kind="sso_refused", actor="1.2.3.4", subject="oidc-co", detail="mismatch", recorded_at=later))

    recent = repo.list_recent()
    assert [f.kind for f in recent] == ["sso_refused", "login_failed"]


def test_auth_facts_list_recent_respects_limit(engine) -> None:  # type: ignore[no-untyped-def]
    repo = AuthFactsRepository(engine)
    for i in range(3):
        repo.create(AuthFact(kind="login_failed", actor="ip", subject=str(i), detail="", recorded_at=_T0))
    assert len(repo.list_recent(limit=2)) == 2


# --- SuperuserBootstrapRepository (issue #94) -----------------------------------


def test_superuser_bootstrap_get_is_none_before_any_write(engine) -> None:  # type: ignore[no-untyped-def]
    repo = SuperuserBootstrapRepository(engine)
    assert repo.get() is None


def test_superuser_bootstrap_upsert_then_get_round_trips(engine, errors: RepoErrorFactory) -> None:  # type: ignore[no-untyped-def]
    users = UserRepository(engine, errors)
    users.create(User(user_id="usr_1", username="a", display_name="A", email=None, role=Role.GUEST, created_at=_T0))
    repo = SuperuserBootstrapRepository(engine)

    repo.upsert(SuperuserBootstrap(email="alice@example.com", claimed_user_id="usr_1", updated_at=_T0))

    row = repo.get()
    assert row == SuperuserBootstrap(email="alice@example.com", claimed_user_id="usr_1", updated_at=_T0)


def test_superuser_bootstrap_upsert_replaces_the_singleton_row(engine) -> None:  # type: ignore[no-untyped-def]
    repo = SuperuserBootstrapRepository(engine)
    repo.upsert(SuperuserBootstrap(email="alice@example.com", claimed_user_id=None, updated_at=_T0))

    later = datetime(2026, 1, 2, tzinfo=UTC)
    repo.upsert(SuperuserBootstrap(email="bob@example.com", claimed_user_id=None, updated_at=later))

    row = repo.get()
    assert row is not None
    assert row.email == "bob@example.com"


def test_superuser_bootstrap_clear_deletes_the_row(engine) -> None:  # type: ignore[no-untyped-def]
    repo = SuperuserBootstrapRepository(engine)
    repo.upsert(SuperuserBootstrap(email="alice@example.com", claimed_user_id=None, updated_at=_T0))

    repo.clear()

    assert repo.get() is None
