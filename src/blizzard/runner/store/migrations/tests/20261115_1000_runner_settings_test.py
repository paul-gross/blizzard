"""Settings expansion preserves old spawn facts across upgrade, downgrade and re-entry."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner import runtime as runner_runtime
from tests.support.config_inputs import init_host

pytestmark = [pytest.mark.component]

_PARENT = "20261111_1000_binding_targets"
_REVISION = "20261115_1000_runner_settings"
_TABLES = {"runner_settings", "runner_setting_entries", "runner_setting_changes"}


def _parent(tmp_path: Path) -> tuple[MigrationRunner, sa.Engine]:
    config = init_host(tmp_path)
    migration = runner_runtime.migration_runner(config)
    migration.downgrade(_PARENT)
    engine = create_engine_from_url(config.db_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO runner_identity (runner_id, runner_name, registered_at) VALUES ('rn_a', 'a', '2026-01-01')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO lease_spawns (lease_id, spawned_at, runner_id) VALUES ('lease-a', '2026-01-01', 'rn_a')"
            )
        )
    return migration, engine


def test_upgrade_preserves_old_spawns_and_downgrade_reupgrade(tmp_path: Path) -> None:
    migration, engine = _parent(tmp_path)
    try:
        migration.upgrade(_REVISION)
        assert set(sa.inspect(engine).get_table_names()) >= _TABLES
        with engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT lease_id, host_settings_revision, runner_settings_revision, transcripts_root FROM lease_spawns"
                )
            ).one()
            assert tuple(row) == ("lease-a", None, None, None)
        migration.downgrade(_PARENT)
        assert not _TABLES & set(sa.inspect(engine).get_table_names())
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT lease_id FROM lease_spawns")).scalar_one() == "lease-a"
        migration.upgrade(_REVISION)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT runner_settings_revision FROM lease_spawns")).scalar_one() is None
    finally:
        engine.dispose()


def test_upgrade_reentry_keeps_settings(tmp_path: Path) -> None:
    migration, engine = _parent(tmp_path)
    try:
        migration.upgrade(_REVISION)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO runner_settings (runner_id, revision, updated_at) VALUES ('rn_a', 1, '2026-01-01')"
                )
            )
            conn.execute(sa.text("UPDATE alembic_version SET version_num = :parent"), {"parent": _PARENT})
        migration.upgrade(_REVISION)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT revision FROM runner_settings")).scalar_one() == 1
    finally:
        engine.dispose()
