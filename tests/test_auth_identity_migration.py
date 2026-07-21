"""The identity-spine migration — ``users``/``identities``/``sessions`` (component
tier, issue #91).

Covers the AC directly: the migration applies both to a fresh store (``base -> head``,
exercised generically by ``test_store_migrations.py``'s whole-ladder test) and to an
*existing* store already at the revision just before this one — and the partial
unique index on ``users.email`` (decision D2) is enforced by sqlite.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema as s

pytestmark = pytest.mark.component

_BEFORE = "20260721_1008_hub_graph_node_session_source"  # the head just before this revision
_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _upgrade_existing_store_to_head(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A store already migrated to the revision *before* this one, then upgraded to
    head — the "existing store" half of the AC (a fresh ``base -> head`` store is
    covered by ``test_store_migrations.py``)."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    runner.upgrade("head")
    return create_engine_from_url(db_url)


def test_tables_and_columns_exist_after_upgrading_an_existing_store(tmp_path: Path) -> None:
    engine = _upgrade_existing_store_to_head(tmp_path)
    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"users", "identities", "sessions"} <= tables

    user_columns = {c["name"] for c in inspector.get_columns("users")}
    assert user_columns == {"id", "username", "display_name", "email", "role", "created_at"}

    identity_columns = {c["name"] for c in inspector.get_columns("identities")}
    assert identity_columns == {"id", "provider_name", "subject", "user_id", "handle", "created_at"}

    session_columns = {c["name"] for c in inspector.get_columns("sessions")}
    assert session_columns == {"id_hash", "user_id", "created_at", "expires_at", "last_seen_at"}


def test_username_is_unique(tmp_path: Path) -> None:
    engine = _upgrade_existing_store_to_head(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(s.users).values(
                id="usr_1", username="ada", display_name="Ada", email=None, role="contributor", created_at=_T0
            )
        )
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            insert(s.users).values(
                id="usr_2", username="ada", display_name="Ada Two", email=None, role="guest", created_at=_T0
            )
        )


def test_email_is_unique_only_when_set(tmp_path: Path) -> None:
    """Decision D2 — the partial unique index (``WHERE email IS NOT NULL``): two
    ``NULL`` emails coexist (sqlite would otherwise treat every ``NULL`` as distinct
    under a plain unique column too, but this pins the *index* is the one doing the
    work — a duplicate non-null email collides)."""
    engine = _upgrade_existing_store_to_head(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(s.users).values(id="usr_1", username="a", display_name="A", email=None, role="guest", created_at=_T0)
        )
        conn.execute(
            insert(s.users).values(id="usr_2", username="b", display_name="B", email=None, role="guest", created_at=_T0)
        )
        conn.execute(
            insert(s.users).values(
                id="usr_3", username="c", display_name="C", email="ada@example.com", role="guest", created_at=_T0
            )
        )
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            insert(s.users).values(
                id="usr_4", username="d", display_name="D", email="ada@example.com", role="guest", created_at=_T0
            )
        )


def test_identity_provider_subject_pair_is_unique(tmp_path: Path) -> None:
    engine = _upgrade_existing_store_to_head(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(s.users).values(id="usr_1", username="a", display_name="A", email=None, role="guest", created_at=_T0)
        )
        conn.execute(
            insert(s.identities).values(
                provider_name="github", subject="123", user_id="usr_1", handle="ada", created_at=_T0
            )
        )
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            insert(s.identities).values(
                provider_name="github", subject="123", user_id="usr_1", handle="ada-renamed", created_at=_T0
            )
        )


def test_downgrade_removes_the_identity_spine_tables(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade("head")
    runner.downgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    tables = set(sa.inspect(engine).get_table_names())
    assert not ({"users", "identities", "sessions"} & tables)
