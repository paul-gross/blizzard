"""The binding-targets revision creates the table empty — a binding recorded under the prior release names no targets
and keeps its row; the downgrade drops the table and a re-upgrade creates it again; a rerun changes nothing.

Stores are seeded from the parent revision's tables, never from ``src/blizzard/runner/store/schema.py``."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner import runtime as runner_runtime
from tests.support.config_inputs import init_host

pytestmark = [pytest.mark.component]

_PARENT = "20261029_1000_takeover_entries"
_REVISION = "20261111_1000_binding_targets"

_T0 = "2026-01-01 00:00:00.000000"


def _insert(conn: sa.Connection, table: str, **values: object) -> None:
    columns = ", ".join(values)
    binds = ", ".join(f":{name}" for name in values)
    conn.execute(sa.text(f"INSERT INTO {table} ({columns}) VALUES ({binds})"), values)


def _store_at_parent(tmp_path: Path) -> tuple[MigrationRunner, sa.Engine]:
    config = init_host(tmp_path)
    migration = runner_runtime.migration_runner(config)
    migration.downgrade(_PARENT)
    engine = create_engine_from_url(config.db_url)
    with engine.begin() as conn:
        _insert(conn, "runner_identity", runner_id="rn_a", runner_name="rn_a", registered_at=_T0)
        _insert(
            conn,
            "env_bindings",
            chunk_id="ch",
            environment_id="e1",
            workdir="/w/e1",
            bound_at=_T0,
            runner_id="rn_a",
            seq=1,
        )
    return migration, engine


def _tables(engine: sa.Engine) -> list[str]:
    return sa.inspect(engine).get_table_names()


def _count(engine: sa.Engine, table: str) -> int:
    with engine.connect() as conn:
        return int(conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one())


def test_the_upgrade_creates_the_table_empty_and_keeps_every_binding(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        migration.upgrade(_REVISION)

        assert _count(engine, "binding_targets") == 0
        assert _count(engine, "env_bindings") == 1
    finally:
        engine.dispose()


def test_the_downgrade_drops_the_table_and_a_reupgrade_creates_it_again(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        migration.upgrade(_REVISION)
        migration.downgrade(_PARENT)
        assert migration.current_revision() == _PARENT
        assert "binding_targets" not in _tables(engine)

        migration.upgrade(_REVISION)
        assert "binding_targets" in _tables(engine)
    finally:
        engine.dispose()


def test_a_rerun_of_an_upgrade_that_already_ran_changes_nothing(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        migration.upgrade(_REVISION)
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE alembic_version SET version_num = :parent"), {"parent": _PARENT})

        migration.upgrade(_REVISION)

        assert migration.current_revision() == _REVISION
        assert _count(engine, "binding_targets") == 0
    finally:
        engine.dispose()
