"""The runner-scope revision adds a nullable ``runner_id`` to every runner-scoped table and fills it from the store's
identity row; a store with no identity keeps it NULL. It creates the three host-scoped tables, and ``env_bindings``'
``workspace_id`` and ``project_id``. A rerun after an interrupted upgrade finishes it; the downgrade restores the
parent's schema exactly; and statements built from the parent's shapes, naming no runner, still write to the migrated
store.

Seeded with rows built from the store's reflected shape at the parent revision, rather than importing
``src/blizzard/runner/store/schema.py``, whose tables keep changing shape after this revision."""

from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from click.testing import CliRunner

from blizzard.cli.main import blizzard
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner import runtime as runner_runtime
from blizzard.runner.store import schema

pytestmark = pytest.mark.component

_PARENT = "20261009_1000_runner_lease_closures_lease_id_index"
_REVISION = "20261011_1000_runner_scope"
_RUNNER_ID = "rn_01JX0000000000000000000000"
_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_HOST_TABLES = {"host_identity", "host_outbound_buffer", "host_high_water_seen"}
_ENV_BINDING_COLUMNS = {"workspace_id", "project_id"}
_ALLOWED_VALUES = re.compile(r"^\s*(\w+)\s+IN\s*\(\s*'([^']*)'")
_HEARTBEATS = 2500  # spans several of the backfill's bounded batches

# Every runner-scoped table but the identity row, which keys itself by its own ``runner_id``.
_KEYED = frozenset(t.name for t in schema.metadata.sorted_tables if t.info["scope"] == "runner") - {"runner_identity"}


def _store_at_parent(tmp_path: Path) -> tuple[MigrationRunner, sa.Engine]:
    config = runner_runtime.init_environment(tmp_path)
    migration = runner_runtime.migration_runner(config)
    migration.downgrade(_PARENT)
    return migration, create_engine_from_url(config.db_url)


def _reflect(engine: sa.Engine) -> sa.MetaData:
    reflected = sa.MetaData()
    reflected.reflect(engine)
    return reflected


def _value_for(column: sa.Column) -> object:
    try:
        python_type = column.type.python_type
    except NotImplementedError:
        return f"{column.name}-1"
    if python_type is bool:
        return False
    if python_type is int:
        return 1
    if python_type is float:
        return 1.0
    if python_type is datetime:
        return _T0
    return f"{column.name}-1"


def _seed_every_keyed_table(engine: sa.Engine, shapes: sa.MetaData) -> None:
    """One row in every keyed table, built from SHAPES: every column gets a value of its own type, or the first value
    its ``IN (...)`` CHECK constraint allows. The shapes name only the columns they reflected, so built from the
    parent's they write exactly as the previous release's statements do."""
    inspector = sa.inspect(engine)
    with engine.begin() as conn:
        for name in sorted(_KEYED):
            table = shapes.tables[name]
            allowed = {
                match.group(1): match.group(2)
                for check in inspector.get_check_constraints(name)
                if (match := _ALLOWED_VALUES.match(check["sqltext"])) is not None
            }
            conn.execute(sa.insert(table).values({c.name: allowed.get(c.name, _value_for(c)) for c in table.columns}))


def _seed_identity(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO runner_identity (runner_id, runner_name, registered_at) VALUES (:id, 'r', :at)"),
            {"id": _RUNNER_ID, "at": _T0.isoformat()},
        )


def _runner_ids(engine: sa.Engine) -> dict[str, set[str | None]]:
    with engine.connect() as conn:
        return {name: set(conn.execute(sa.text(f"SELECT DISTINCT runner_id FROM {name}")).scalars()) for name in _KEYED}


def _tables(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names()) - {"alembic_version"}


def _row_counts(engine: sa.Engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {name: conn.execute(sa.text(f"SELECT count(*) FROM {name}")).scalar_one() for name in _tables(engine)}


def _schema(engine: sa.Engine) -> dict[str, object]:
    inspector = sa.inspect(engine)
    return {
        table: (
            tuple((c["name"], str(c["type"]), c["nullable"], c["default"]) for c in inspector.get_columns(table)),
            (
                inspector.get_pk_constraint(table)["name"],
                tuple(inspector.get_pk_constraint(table)["constrained_columns"]),
            ),
            frozenset((tuple(i["column_names"]), i["unique"], i["name"]) for i in inspector.get_indexes(table)),
            frozenset((tuple(u["column_names"]), u["name"]) for u in inspector.get_unique_constraints(table)),
            frozenset((c["name"], c["sqltext"]) for c in inspector.get_check_constraints(table)),
            frozenset(
                (tuple(fk["constrained_columns"]), fk["referred_table"], fk["name"])
                for fk in inspector.get_foreign_keys(table)
            ),
        )
        for table in _tables(engine)
    }


def test_the_revision_keys_exactly_the_runner_scoped_tables(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        before = _tables(engine)
        migration.upgrade(_REVISION)

        assert _tables(engine) - before == _HOST_TABLES
        inspector = sa.inspect(engine)
        keyed = {t for t in _tables(engine) if "runner_id" in {c["name"] for c in inspector.get_columns(t)}}
        assert keyed == _KEYED | {"runner_identity"}
        for table in _KEYED:
            [runner_id] = [c for c in inspector.get_columns(table) if c["name"] == "runner_id"]
            assert runner_id["nullable"], table
        added = {c["name"] for c in inspector.get_columns("env_bindings")} & _ENV_BINDING_COLUMNS
        assert added == _ENV_BINDING_COLUMNS
        assert all(c["nullable"] for c in inspector.get_columns("env_bindings") if c["name"] in _ENV_BINDING_COLUMNS)
    finally:
        engine.dispose()


def test_upgrade_fills_every_runner_scoped_row_from_the_identity(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        _seed_identity(engine)
        _seed_every_keyed_table(engine, _reflect(engine))
        with engine.begin() as conn:
            conn.execute(
                sa.text("INSERT INTO heartbeats (lease_id, beat_at) VALUES ('lease-1', :at)"),
                [{"at": _T0.isoformat()} for _ in range(_HEARTBEATS)],
            )
        counts = _row_counts(engine)

        migration.upgrade(_REVISION)

        assert _runner_ids(engine) == {table: {_RUNNER_ID} for table in _KEYED}
        assert {t: n for t, n in _row_counts(engine).items() if t in counts} == counts
        with engine.connect() as conn:
            stamped = conn.execute(sa.text("SELECT count(*) FROM heartbeats WHERE runner_id = :id"), {"id": _RUNNER_ID})
            assert stamped.scalar_one() == _HEARTBEATS + 1
    finally:
        engine.dispose()


def test_a_store_with_no_identity_keeps_every_runner_id_null(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        _seed_every_keyed_table(engine, _reflect(engine))

        migration.upgrade(_REVISION)

        assert _runner_ids(engine) == {table: {None} for table in _KEYED}
    finally:
        engine.dispose()


def test_a_rerun_after_an_interrupted_upgrade_finishes_it(tmp_path: Path) -> None:
    """Re-entry: some tables already carry the column, some rows are already stamped, and one host table already
    exists — the upgrade skips what is done and completes the rest."""
    migration, engine = _store_at_parent(tmp_path)
    try:
        _seed_identity(engine)
        _seed_every_keyed_table(engine, _reflect(engine))
        done = sorted(_KEYED)[: len(_KEYED) // 2]
        with engine.begin() as conn:
            for table in done:
                conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN runner_id VARCHAR"))
            conn.execute(sa.text(f"UPDATE {done[0]} SET runner_id = :id"), {"id": _RUNNER_ID})
            conn.execute(sa.text("ALTER TABLE env_bindings ADD COLUMN workspace_id VARCHAR"))
            conn.execute(
                sa.text(
                    "CREATE TABLE host_identity (id INTEGER NOT NULL PRIMARY KEY, host_id VARCHAR NOT NULL,"
                    " tenant_id VARCHAR, registered_at DATETIME NOT NULL)"
                )
            )

        migration.upgrade(_REVISION)

        assert migration.current_revision() == _REVISION
        assert _runner_ids(engine) == {table: {_RUNNER_ID} for table in _KEYED}
        assert _tables(engine) >= _HOST_TABLES
        assert {c["name"] for c in sa.inspect(engine).get_columns("env_bindings")} >= _ENV_BINDING_COLUMNS
    finally:
        engine.dispose()


def test_downgrade_restores_the_parent_schema_exactly(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        _seed_identity(engine)
        _seed_every_keyed_table(engine, _reflect(engine))
        schema_before, counts_before = _schema(engine), _row_counts(engine)

        migration.upgrade(_REVISION)
        migration.downgrade(_PARENT)

        assert _schema(engine) == schema_before
        assert _row_counts(engine) == counts_before
    finally:
        engine.dispose()


def test_the_previous_releases_statements_write_to_the_migrated_store_with_no_runner(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        _seed_identity(engine)
        parent_shapes = _reflect(engine)
        migration.upgrade(_REVISION)

        _seed_every_keyed_table(engine, parent_shapes)

        assert _runner_ids(engine) == {table: {None} for table in _KEYED}
    finally:
        engine.dispose()


def test_the_cli_migrates_a_copied_store_down_to_the_parent_and_back(tmp_path: Path) -> None:
    original = tmp_path / "original"
    config = runner_runtime.init_environment(original)
    runner_runtime.migration_runner(config).downgrade(_PARENT)
    engine = create_engine_from_url(config.db_url)
    try:
        _seed_identity(engine)
        _seed_every_keyed_table(engine, _reflect(engine))
        parent_schema = _schema(engine)
    finally:
        engine.dispose()
    copy = tmp_path / "copy"
    shutil.copytree(original, copy)
    # The scaffold names its store by absolute path, so the copy is repointed at its own, as a rehearsal must be.
    copy_url = f"sqlite:///{copy / 'data' / 'runner.db'}"
    config_path = copy / "blizzard-runner.toml"
    config_path.write_text(config_path.read_text().replace(f'db_url = "{config.db_url}"', f'db_url = "{copy_url}"'))
    cli = CliRunner()

    def migrate(*args: str) -> None:
        result = cli.invoke(blizzard, ["runner", "migrate", "--dir", str(copy), *args])
        assert result.exit_code == 0, result.output

    engine = create_engine_from_url(copy_url)
    try:
        migrate()
        assert _runner_ids(engine) == {table: {_RUNNER_ID} for table in _KEYED}

        migrate("--down", _PARENT)
        assert _schema(engine) == parent_schema

        migrate()
        assert _runner_ids(engine) == {table: {_RUNNER_ID} for table in _KEYED}
        assert _tables(engine) >= _HOST_TABLES
    finally:
        engine.dispose()
    original_engine = create_engine_from_url(config.db_url)
    try:
        assert _schema(original_engine) == parent_schema
    finally:
        original_engine.dispose()
