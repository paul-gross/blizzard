"""The usage-limit-floor revision creates the four empty floor-fact tables — the subscription marks and clears and
their runner-scoped twins — and changes nothing else; its downgrade drops them and restores the parent's schema; a rerun
of an upgrade that already ran changes nothing.

Stores are seeded from the shapes reflected at the parent revision, never from ``src/blizzard/runner/store/schema.py``,
whose tables keep changing shape after this revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner import runtime as runner_runtime
from tests.config_inputs import init_host
from tests.runner_scope_seeding import T0, reflect, seed_every_keyed_table

pytestmark = [pytest.mark.component]

_PARENT = "20261019_1000_served_project_color"

_REVISION = "20261022_1000_usage_limit_floor"

_MARKS = ("subscription_usage_limits", "runner_usage_limits")
_CLEARS = ("subscription_floor_clears", "runner_floor_clears")
_NEW = frozenset(_MARKS + _CLEARS)

_MARK_COLUMNS = (
    ("marked_at", "DATETIME", False),
    ("resets_at", "DATETIME", True),
    ("window", "VARCHAR", True),
    ("window_resets_at", "DATETIME", True),
    ("harness_id", "VARCHAR", False),
    ("detail", "TEXT", False),
    ("lease_id", "VARCHAR", False),
    ("epoch", "INTEGER", False),
    ("generation", "INTEGER", False),
    ("invocation_kind", "VARCHAR", False),
)

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


def test_the_upgrade_creates_the_four_empty_floor_tables_and_changes_nothing_else(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        before, counts = _schema(engine), _row_counts(engine)

        migration.upgrade(_REVISION)

        after = _schema(engine)
        assert set(after) - set(before) == _NEW
        assert {k: v for k, v in after.items() if k not in _NEW} == before
        for table, key in zip(_MARKS, ("slug", "runner_id"), strict=True):
            columns, pk, indexes, uniques = after[table]  # type: ignore[misc]
            assert columns == (("id", "INTEGER", False), (key, "VARCHAR", False), *_MARK_COLUMNS)
            assert pk == ("id",)
            assert ((key, "marked_at"), False, f"ix_{table}_{key}_marked_at") in indexes
            invocation = ("lease_id", "epoch", "generation", "invocation_kind")
            assert uniques == {(invocation if key == "slug" else (key, *invocation), f"uq_{table}_invocation")}
        for table, key in zip(_CLEARS, ("slug", "runner_id"), strict=True):
            columns, pk, indexes, _ = after[table]  # type: ignore[misc]
            assert columns == (
                ("id", "INTEGER", False),
                (key, "VARCHAR", False),
                ("cleared_at", "DATETIME", False),
                ("by", "VARCHAR", False),
            )
            assert pk == ("id",)
            assert indexes == {((key, "cleared_at"), False, f"ix_{table}_{key}_cleared_at")}
        assert _row_counts(engine) == {**counts, **dict.fromkeys(_NEW, 0)}
    finally:
        engine.dispose()


def test_a_replayed_invocation_is_refused_by_the_table_itself(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        migration.upgrade(_REVISION)
        insert = sa.text(
            "INSERT INTO subscription_usage_limits (slug, marked_at, harness_id, detail, lease_id, epoch, generation,"
            " invocation_kind) VALUES ('anthropic', :at, 'claude-code', 'limit', 'lease_1', 1, 1, 'worker')"
        )
        with engine.begin() as conn:
            conn.execute(insert, {"at": T0.isoformat()})
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(insert, {"at": T0.isoformat()})
    finally:
        engine.dispose()


def test_the_downgrade_drops_the_floor_tables_and_restores_the_parent_schema(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        before, counts = _schema(engine), _row_counts(engine)
        migration.upgrade(_REVISION)
        with engine.begin() as conn:
            conn.execute(
                sa.text("INSERT INTO runner_floor_clears (runner_id, cleared_at, by) VALUES (:id, :at, 'operator')"),
                {"id": _RUNNER_ID, "at": T0.isoformat()},
            )

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
