"""The host settings tables arrive empty, leave on downgrade, and a re-entered upgrade keeps their rows."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner import runtime as runner_runtime
from tests.support.config_inputs import init_host

pytestmark = [pytest.mark.component]

_PARENT = "20261115_1000_runner_settings"
_REVISION = "20261118_1000_host_settings"
_TABLES = {"host_settings", "host_setting_entries", "host_setting_changes"}


def _parent(tmp_path: Path) -> tuple[MigrationRunner, sa.Engine]:
    config = init_host(tmp_path)
    migration = runner_runtime.migration_runner(config)
    migration.downgrade(_PARENT)
    engine = create_engine_from_url(config.db_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO runner_settings (runner_id, revision, updated_at) VALUES ('rn_a', 2, '2026-01-01')")
        )
    return migration, engine


def test_upgrade_adds_empty_tables_and_downgrade_removes_them(tmp_path: Path) -> None:
    migration, engine = _parent(tmp_path)
    try:
        migration.upgrade(_REVISION)
        assert set(sa.inspect(engine).get_table_names()) >= _TABLES
        with engine.connect() as conn:
            for table in _TABLES:
                assert conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one() == 0
        migration.downgrade(_PARENT)
        assert not _TABLES & set(sa.inspect(engine).get_table_names())
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT revision FROM runner_settings")).scalar_one() == 2
    finally:
        engine.dispose()


def test_the_record_holds_one_row(tmp_path: Path) -> None:
    migration, engine = _parent(tmp_path)
    try:
        migration.upgrade(_REVISION)
        with engine.begin() as conn:
            conn.execute(sa.text("INSERT INTO host_settings (id, revision, updated_at) VALUES (1, 1, '2026-01-01')"))
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(sa.text("INSERT INTO host_settings (id, revision, updated_at) VALUES (2, 1, '2026-01-01')"))
    finally:
        engine.dispose()


def test_upgrade_reentry_keeps_settings(tmp_path: Path) -> None:
    migration, engine = _parent(tmp_path)
    try:
        migration.upgrade(_REVISION)
        with engine.begin() as conn:
            conn.execute(sa.text("INSERT INTO host_settings (id, revision, updated_at) VALUES (1, 3, '2026-01-01')"))
            conn.execute(
                sa.text(
                    "INSERT INTO host_setting_entries (key, value, at_default, revision) "
                    "VALUES ('tick_seconds', '5', 0, 3)"
                )
            )
            conn.execute(sa.text("UPDATE alembic_version SET version_num = :parent"), {"parent": _PARENT})
        migration.upgrade(_REVISION)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT revision FROM host_settings")).scalar_one() == 3
            assert conn.execute(sa.text("SELECT value FROM host_setting_entries")).scalar_one() == "5"
    finally:
        engine.dispose()
