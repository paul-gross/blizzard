"""The identity-spine SQLAlchemy adapters — real store, injected errors (component
tier, issue #91).

Mirrors ``tests/test_runner_enrollment.py``'s own real-store shape for the fleet
registry: each adapter is exercised over a migrated sqlite engine, with the injected
:class:`~blizzard.hub.auth.errors.RepoErrorFactory` proven to wrap a raced/unexpected
``IntegrityError`` into the domain :class:`~blizzard.hub.auth.errors.RepoError`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import structlog

from blizzard.auth_core import Role
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.auth.errors import RepoError, RepoErrorFactory
from blizzard.hub.auth.internal.identity_repository import IdentityRepository
from blizzard.hub.auth.internal.session_repository import SessionRepository
from blizzard.hub.auth.internal.user_repository import UserRepository
from blizzard.hub.auth.models import Identity, Session, User
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
