"""The sealed-token table is additive across upgrade, downgrade, re-upgrade and re-entry."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner import runtime as runner_runtime
from tests.support.config_inputs import init_host

pytestmark = [pytest.mark.component]

_PARENT = "20261118_1000_host_settings"
_REVISION = "20261118_1000_runner_hub_tokens"
_TABLE = "runner_hub_tokens"
_INSERT = sa.text(
    "INSERT INTO runner_hub_tokens (runner_id, ciphertext, nonce, key_id, revision, updated_at)"
    " VALUES ('rn_a', 'c', 'n', 'k', 1, '2026-01-01')"
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
        assert _TABLE not in sa.inspect(engine).get_table_names()
        migration.upgrade(_REVISION)
        assert _TABLE in sa.inspect(engine).get_table_names()
        with engine.begin() as conn:
            conn.execute(_INSERT)
        migration.downgrade(_PARENT)
        assert _TABLE not in sa.inspect(engine).get_table_names()
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT runner_name FROM runner_identity")).scalar_one() == "a"
        migration.upgrade(_REVISION)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT count(*) FROM runner_hub_tokens")).scalar_one() == 0
    finally:
        engine.dispose()


def test_upgrade_reentry_keeps_tokens(tmp_path: Path) -> None:
    migration, engine = _parent(tmp_path)
    try:
        migration.upgrade(_REVISION)
        with engine.begin() as conn:
            conn.execute(_INSERT)
            conn.execute(sa.text("UPDATE alembic_version SET version_num = :parent"), {"parent": _PARENT})
        migration.upgrade(_REVISION)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT revision FROM runner_hub_tokens")).scalar_one() == 1
    finally:
        engine.dispose()
