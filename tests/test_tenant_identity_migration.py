"""The tenant identity move: each user's role becomes a membership in the store's only tenant — the superuser an admin
member and the hub administrator, a pending user nothing — ``users.role`` goes, and the superuser claim and every
session stay; a re-run over a stopped move writes no fact twice; the copy pages in bounded batches; the downgrade, with
one tenant only, restores the store exactly."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.script import ScriptDirectory
from sqlalchemy import event

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import MIGRATIONS_DIR
from tests.test_store_migrations import _schema_snapshot

pytestmark = [pytest.mark.component, pytest.mark.migration_revision("20261008_1700_tenant_identity_move")]

_TABLES = "20261008_1650_identity_tables"
_MOVE = "20261008_1700_tenant_identity_move"
_SECOND_TENANT = "ten_01J9Z3M0P8QK7V2S4W6X8Y0A1C"
_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_ROLES = {
    "usr_guest": "guest",
    "usr_contributor": "contributor",
    "usr_admin": "admin",
    "usr_superuser": "superuser",
    "usr_pending": "pending",
}
_INSERT_USER = sa.text(
    "INSERT INTO users (id, username, display_name, email, role, created_at) VALUES (:id, :id, :id, NULL, :role, :at)"
)


def _batch_rows() -> int:
    revision = ScriptDirectory(str(MIGRATIONS_DIR)).get_revision(_MOVE)
    assert revision is not None
    return revision.module._BATCH_ROWS


def _at_the_tables(tmp_path: Path) -> tuple[MigrationRunner, sa.Engine, str]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_TABLES)
    engine = create_engine_from_url(db_url)
    with engine.connect() as conn:
        [tenant] = conn.execute(sa.text("SELECT tenant_id FROM tenants")).scalars().all()
    return runner, engine, tenant


def _seed(engine: sa.Engine) -> None:
    """One user of every role, a live session of the superuser's, and the superuser's claim."""
    with engine.begin() as conn:
        conn.execute(_INSERT_USER, [{"id": user_id, "role": role, "at": _T0} for user_id, role in _ROLES.items()])
        conn.execute(
            sa.text(
                "INSERT INTO sessions (id_hash, user_id, created_at, expires_at, last_seen_at)"
                " VALUES ('hash_su', 'usr_superuser', :at, :expires, :at)"
            ),
            {"at": _T0, "expires": _T0 + timedelta(days=1)},
        )
        conn.execute(
            sa.text(
                "INSERT INTO superuser_bootstrap (id, email, claimed_user_id, updated_at)"
                " VALUES (1, 'su@example.com', 'usr_superuser', :at)"
            ),
            {"at": _T0},
        )


def _rows(engine: sa.Engine, query: str) -> list[tuple[Any, ...]]:
    with engine.connect() as conn:
        return [tuple(row) for row in conn.execute(sa.text(query))]


def _memberships(engine: sa.Engine) -> list[tuple[Any, ...]]:
    return _rows(engine, "SELECT tenant_id, user_id, role, set_by FROM membership_facts ORDER BY user_id")


def _hub_admins(engine: sa.Engine) -> list[tuple[Any, ...]]:
    return _rows(engine, "SELECT user_id, granted, set_by FROM hub_admin_facts ORDER BY user_id")


def _user_columns(engine: sa.Engine) -> set[str]:
    return {str(column["name"]) for column in sa.inspect(engine).get_columns("users")}


@contextmanager
def _inserts_into(table: str) -> Iterator[list[int]]:
    """The rows each ``INSERT`` into ``table`` any engine runs while the context is open wrote."""
    inserts: list[int] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        words = statement.split()
        if words[0].upper() == "INSERT" and words[2] == table:
            inserts.append(len(parameters) if executemany else 1)

    event.listen(sa.Engine, "after_cursor_execute", record)
    try:
        yield inserts
    finally:
        event.remove(sa.Engine, "after_cursor_execute", record)


def test_each_role_becomes_a_membership_and_the_superuser_the_hub_administrator(tmp_path: Path) -> None:
    runner, engine, tenant = _at_the_tables(tmp_path)
    try:
        _seed(engine)
        claim = _rows(engine, "SELECT * FROM superuser_bootstrap")
        sessions = _rows(engine, "SELECT * FROM sessions")

        runner.upgrade(_MOVE)

        assert _memberships(engine) == [
            (tenant, "usr_admin", "admin", "migration"),
            (tenant, "usr_contributor", "contributor", "migration"),
            (tenant, "usr_guest", "guest", "migration"),
            (tenant, "usr_superuser", "admin", "migration"),
        ]
        assert _hub_admins(engine) == [("usr_superuser", 1, "migration")]
        assert "role" not in _user_columns(engine)
        assert _rows(engine, "SELECT id FROM users ORDER BY id") == [(user_id,) for user_id in sorted(_ROLES)]
        assert _rows(engine, "SELECT * FROM superuser_bootstrap") == claim
        assert _rows(engine, "SELECT * FROM sessions") == sessions
    finally:
        engine.dispose()


def test_a_rerun_over_a_stopped_move_writes_no_fact_twice(tmp_path: Path) -> None:
    runner, engine, tenant = _at_the_tables(tmp_path)
    try:
        _seed(engine)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO membership_facts (tenant_id, user_id, role, set_at, set_by)"
                    " VALUES (:t, 'usr_superuser', 'admin', :at, 'migration'),"
                    " (:t, 'usr_guest', 'guest', :at, 'migration')"
                ),
                {"t": tenant, "at": _T0},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO hub_admin_facts (user_id, granted, set_at, set_by)"
                    " VALUES ('usr_superuser', 1, :at, 'migration')"
                ),
                {"at": _T0},
            )

        runner.upgrade(_MOVE)

        assert [row[1] for row in _memberships(engine)] == [
            "usr_admin",
            "usr_contributor",
            "usr_guest",
            "usr_superuser",
        ]
        assert _hub_admins(engine) == [("usr_superuser", 1, "migration")]
    finally:
        engine.dispose()


def test_the_copy_and_its_reverse_each_page_in_bounded_batches(tmp_path: Path) -> None:
    runner, engine, _ = _at_the_tables(tmp_path)
    batch = _batch_rows()
    count = 2 * batch + 3
    try:
        with engine.begin() as conn:
            conn.execute(_INSERT_USER, [{"id": f"usr_{n:05d}", "role": "guest", "at": _T0} for n in range(count)])

        with _inserts_into("membership_facts") as inserts:
            runner.upgrade(_MOVE)
        assert len(inserts) == 3 and max(inserts) <= batch and sum(inserts) == count

        runner.downgrade(_TABLES)
        assert _rows(engine, "SELECT DISTINCT role FROM users") == [("guest",)]
    finally:
        engine.dispose()


def test_the_downgrade_refuses_while_a_second_tenant_exists_naming_both(tmp_path: Path) -> None:
    runner, engine, tenant = _at_the_tables(tmp_path)
    try:
        runner.upgrade(_MOVE)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO tenants (tenant_id, name, created_at, created_by) VALUES (:t, 'other', :at, 'usr_1')"
                ),
                {"t": _SECOND_TENANT, "at": _T0},
            )

        with pytest.raises(RuntimeError, match="delete every tenant but one before downgrading") as refused:
            runner.downgrade(_TABLES)

        assert tenant in str(refused.value) and _SECOND_TENANT in str(refused.value)
    finally:
        engine.dispose()


def test_with_one_tenant_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    runner, engine, _ = _at_the_tables(tmp_path)
    try:
        _seed(engine)
        users = _rows(engine, "SELECT id, username, display_name, email, role, created_at FROM users ORDER BY id")
        schema = _schema_snapshot(engine, exemptions={}, name_exemptions={})

        runner.upgrade(_MOVE)
        runner.downgrade(_TABLES)

        assert _rows(engine, "SELECT id, username, display_name, email, role, created_at FROM users ORDER BY id") == (
            users
        )
        assert _memberships(engine) == [] and _hub_admins(engine) == []
        assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == schema
    finally:
        engine.dispose()


def test_the_downgrade_reads_each_role_from_the_facts_in_force(tmp_path: Path) -> None:
    """A membership changed or revoked after the upgrade, and a hub administrator granted after it, are what
    ``users.role`` holds again."""
    runner, engine, tenant = _at_the_tables(tmp_path)
    try:
        _seed(engine)
        runner.upgrade(_MOVE)
        later = _T0 + timedelta(days=1)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO membership_facts (tenant_id, user_id, role, set_at, set_by)"
                    " VALUES (:t, 'usr_guest', 'contributor', :at, 'usr_admin'), (:t, 'usr_admin', NULL, :at, 'x')"
                ),
                {"t": tenant, "at": later},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO hub_admin_facts (user_id, granted, set_at, set_by)"
                    " VALUES ('usr_pending', 1, :at, 'usr_superuser')"
                ),
                {"at": later},
            )

        runner.downgrade(_TABLES)

        assert dict(_rows(engine, "SELECT id, role FROM users")) == {
            "usr_guest": "contributor",
            "usr_contributor": "contributor",
            "usr_admin": "pending",
            "usr_superuser": "superuser",
            "usr_pending": "superuser",
        }
    finally:
        engine.dispose()
