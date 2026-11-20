"""The host-record tables are additive across upgrade, downgrade, re-upgrade and re-entry."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner import runtime as runner_runtime
from tests.support.config_inputs import init_host

pytestmark = [pytest.mark.component]

_PARENT = "20261119_1000_hosted_runners"
_REVISION = "20261120_1000_host_records"
_TABLES = {"host_workspaces", "host_workspace_facts", "host_subscriptions", "host_subscription_facts"}
_INSERTS = (
    sa.text(
        "INSERT INTO host_workspaces (workspace_id, revision, settings, created_at)"
        " VALUES ('ws-a', 1, '{}', '2026-01-01')"
    ),
    sa.text(
        "INSERT INTO host_workspace_facts (workspace_id, recorded_at, op, actor, door)"
        " VALUES ('ws-a', '2026-01-01', 'retired', 'operator', 'cli')"
    ),
    sa.text(
        "INSERT INTO host_subscriptions (slug, provider, revision, settings, created_at)"
        " VALUES ('sub-a', 'anthropic', 1, '{}', '2026-01-01')"
    ),
    sa.text(
        "INSERT INTO host_subscription_facts (slug, recorded_at, op, actor, door)"
        " VALUES ('sub-a', '2026-01-01', 'retired', 'operator', 'cli')"
    ),
)


def _parent(tmp_path: Path) -> tuple[MigrationRunner, sa.Engine]:
    config = init_host(tmp_path)
    migration = runner_runtime.migration_runner(config)
    migration.downgrade(_PARENT)
    engine = create_engine_from_url(config.db_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO hosted_runners (runner_id, added_at, added_by) VALUES ('rn_a', '2026-01-01', 'seed')")
        )
    return migration, engine


def test_upgrade_downgrade_reupgrade_keeps_other_rows(tmp_path: Path) -> None:
    migration, engine = _parent(tmp_path)
    try:
        assert not _TABLES & set(sa.inspect(engine).get_table_names())
        migration.upgrade(_REVISION)
        assert set(sa.inspect(engine).get_table_names()) >= _TABLES
        with engine.begin() as conn:
            for insert in _INSERTS:
                conn.execute(insert)
        migration.downgrade(_PARENT)
        assert not _TABLES & set(sa.inspect(engine).get_table_names())
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT added_by FROM hosted_runners")).scalar_one() == "seed"
        migration.upgrade(_REVISION)
        with engine.connect() as conn:
            for table in _TABLES:
                assert conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    finally:
        engine.dispose()


def test_upgrade_reentry_keeps_record_rows(tmp_path: Path) -> None:
    migration, engine = _parent(tmp_path)
    try:
        migration.upgrade(_REVISION)
        with engine.begin() as conn:
            for insert in _INSERTS:
                conn.execute(insert)
            conn.execute(sa.text("UPDATE alembic_version SET version_num = :parent"), {"parent": _PARENT})
        migration.upgrade(_REVISION)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT workspace_id FROM host_workspaces")).scalar_one() == "ws-a"
            assert conn.execute(sa.text("SELECT provider FROM host_subscriptions")).scalar_one() == "anthropic"
            assert conn.execute(sa.text("SELECT op FROM host_workspace_facts")).scalar_one() == "retired"
            assert conn.execute(sa.text("SELECT op FROM host_subscription_facts")).scalar_one() == "retired"
    finally:
        engine.dispose()
