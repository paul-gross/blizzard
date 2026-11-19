"""The hosted-runner tables are additive across upgrade, downgrade, re-upgrade and re-entry."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner import runtime as runner_runtime
from tests.support.config_inputs import init_host

pytestmark = [pytest.mark.component]

_PARENT = "20261118_1000_runner_hub_tokens"
_REVISION = "20261119_1000_hosted_runners"
_TABLES = {"hosted_runners", "hosted_runner_facts"}
_INSERTS = (
    sa.text("INSERT INTO hosted_runners (runner_id, added_at, added_by) VALUES ('rn_a', '2026-01-01', 'seed')"),
    sa.text(
        "INSERT INTO hosted_runner_facts (runner_id, recorded_at, op, actor, door)"
        " VALUES ('rn_a', '2026-01-01', 'added', 'seed', 'seed')"
    ),
)


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
            assert conn.execute(sa.text("SELECT runner_name FROM runner_identity")).scalar_one() == "a"
        migration.upgrade(_REVISION)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT count(*) FROM hosted_runners")).scalar_one() == 0
            assert conn.execute(sa.text("SELECT count(*) FROM hosted_runner_facts")).scalar_one() == 0
    finally:
        engine.dispose()


def test_upgrade_reentry_keeps_hosted_rows(tmp_path: Path) -> None:
    migration, engine = _parent(tmp_path)
    try:
        migration.upgrade(_REVISION)
        with engine.begin() as conn:
            for insert in _INSERTS:
                conn.execute(insert)
            conn.execute(sa.text("UPDATE alembic_version SET version_num = :parent"), {"parent": _PARENT})
        migration.upgrade(_REVISION)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT added_by FROM hosted_runners")).scalar_one() == "seed"
            assert conn.execute(sa.text("SELECT op FROM hosted_runner_facts")).scalar_one() == "added"
    finally:
        engine.dispose()
