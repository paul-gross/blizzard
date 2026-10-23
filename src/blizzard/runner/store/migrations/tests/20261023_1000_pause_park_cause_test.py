"""The pause-park-cause revision adds a nullable ``pause_parks.cause``, NULL on every existing row, and
changes nothing else; its downgrade drops it and restores the parent's schema with every row kept; a rerun of an
upgrade that already ran changes nothing.

Stores are seeded from the shapes reflected at the parent revision, never from ``src/blizzard/runner/store/schema.py``,
whose tables keep changing shape after this revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner import runtime as runner_runtime
from tests.config_inputs import init_host
from tests.runner_scope_seeding import T0, reflect, seed_every_keyed_table

pytestmark = [pytest.mark.component]

_PARENT = "20261022_1000_usage_limit_floor"

_REVISION = "20261023_1000_pause_park_cause"

#: The runner every seeded row names, as the seeding helper writes a string column.
_RUNNER_ID = "runner_id-1"


def _store_at_parent(tmp_path: Path) -> tuple[MigrationRunner, sa.Engine]:
    """A parent-revision store holding one identity and one row in every keyed table."""
    config = init_host(tmp_path)
    migration = runner_runtime.migration_runner(config)
    migration.downgrade(_PARENT)
    engine = create_engine_from_url(config.db_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO runner_identity (runner_id, runner_name, registered_at) VALUES (:id, 'r', :at)"),
            {"id": _RUNNER_ID, "at": T0.isoformat()},
        )
    seed_every_keyed_table(engine, reflect(engine))
    return migration, engine


def _tables(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names()) - {"alembic_version"}


def _schema(engine: sa.Engine) -> dict[str, object]:
    inspector = sa.inspect(engine)
    return {
        table: (
            tuple((c["name"], str(c["type"]), c["nullable"]) for c in inspector.get_columns(table)),
            tuple(inspector.get_pk_constraint(table)["constrained_columns"]),
            frozenset((tuple(i["column_names"]), i["unique"], i["name"]) for i in inspector.get_indexes(table)),
            frozenset((tuple(u["column_names"]), u["name"]) for u in inspector.get_unique_constraints(table)),
        )
        for table in _tables(engine)
    }


def _row_counts(engine: sa.Engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {name: conn.execute(sa.text(f"SELECT count(*) FROM {name}")).scalar_one() for name in _tables(engine)}


def test_the_upgrade_adds_a_null_cause_to_pause_parks_and_changes_nothing_else(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        before, counts = _schema(engine), _row_counts(engine)

        migration.upgrade(_REVISION)

        after = _schema(engine)
        assert {k: v for k, v in after.items() if k != "pause_parks"} == {
            k: v for k, v in before.items() if k != "pause_parks"
        }
        columns, key, indexes, uniques = after["pause_parks"]  # type: ignore[misc]
        old_columns, old_key, old_indexes, old_uniques = before["pause_parks"]  # type: ignore[misc]
        assert columns == (*old_columns, ("cause", "VARCHAR", True))
        assert (key, indexes, uniques) == (old_key, old_indexes, old_uniques)
        assert _row_counts(engine) == counts
        with engine.connect() as conn:
            causes = conn.execute(sa.text("SELECT cause FROM pause_parks")).scalars().all()
        assert causes and all(c is None for c in causes)
    finally:
        engine.dispose()


def test_the_downgrade_drops_cause_and_restores_the_parent_schema(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        before, counts = _schema(engine), _row_counts(engine)
        migration.upgrade(_REVISION)
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE pause_parks SET cause = 'usage-limit'"))

        migration.downgrade(_PARENT)

        assert migration.current_revision() == _PARENT
        assert _schema(engine) == before
        assert _row_counts(engine) == counts
    finally:
        engine.dispose()


def test_a_rerun_of_an_upgrade_that_already_ran_changes_nothing(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        migration.upgrade(_REVISION)
        schema, counts = _schema(engine), _row_counts(engine)
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE alembic_version SET version_num = :parent"), {"parent": _PARENT})

        migration.upgrade(_REVISION)

        assert migration.current_revision() == _REVISION
        assert (_schema(engine), _row_counts(engine)) == (schema, counts)
    finally:
        engine.dispose()
