"""The flows revision: six tenant-keyed tables are created as the schema declares them, empty; a re-run changes nothing;
and the downgrade restores the parent's schema exactly, the upgrade walking up again after it."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store import schema as s
from tests.migration_support_store_migrations import schema_snapshot
from tests.support import migrate_to

pytestmark = [pytest.mark.component]

_REVISION = importlib.import_module("blizzard.hub.store.migrations.versions.20261027_0900_flows")
_PARENT = _REVISION.down_revision
_FLOWS = _REVISION.revision
_NEW_TABLES = {
    "flows",
    "flow_submissions",
    "flow_submission_flows",
    "flow_submission_closures",
    "hub_turns",
    "flow_facts",
}


def _tables(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names())


def _shape(engine: sa.Engine, table: str) -> tuple[object, ...]:
    inspector = sa.inspect(engine)
    return (
        {c["name"]: (str(c["type"]), bool(c["nullable"])) for c in inspector.get_columns(table)},
        inspector.get_pk_constraint(table)["constrained_columns"],
        {(u["name"], tuple(u["column_names"])) for u in inspector.get_unique_constraints(table)},
        {(i["name"], tuple(i["column_names"])) for i in inspector.get_indexes(table)},
        {(f["name"], tuple(f["constrained_columns"]), f["referred_table"]) for f in inspector.get_foreign_keys(table)},
    )


def _snapshot(engine: sa.Engine) -> dict[str, object]:
    return schema_snapshot(engine, exemptions={}, name_exemptions={})


def test_the_upgrade_creates_the_six_tables_empty(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    assert not _tables(engine) & _NEW_TABLES

    runner.upgrade(_FLOWS)

    assert _tables(engine) >= _NEW_TABLES
    with engine.connect() as conn:
        for table in _NEW_TABLES:
            assert conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar() == 0
    inspector = sa.inspect(engine)
    assert {(u["name"], tuple(u["column_names"])) for u in inspector.get_unique_constraints("flow_submissions")} == {
        (
            "uq_flow_submissions_tenant_visit_generation",
            ("tenant_id", "chunk_id", "node_id", "epoch", "generation"),
        ),
        ("uq_flow_submissions_tenant_arrival", ("tenant_id", "arrival")),
    }
    assert {i["name"] for i in inspector.get_indexes("hub_turns")} == {
        "ix_hub_turns_tenant_submission_id",
        "ix_hub_turns_tenant_ended_at",
    }


def test_the_new_tables_match_the_declared_schema(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    runner.upgrade(_FLOWS)
    declared = create_engine_from_url(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        s.metadata.create_all(declared)
        for table in _NEW_TABLES:
            assert _shape(engine, table) == _shape(declared, table)
    finally:
        declared.dispose()


def test_a_rerun_over_a_store_already_reshaped_changes_nothing(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, _FLOWS)
    schema = _snapshot(engine)

    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        _REVISION.upgrade()

    assert _snapshot(engine) == schema


def test_the_downgrade_restores_the_parent_schema_and_walks_up_again(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    before = _snapshot(engine)
    runner.upgrade(_FLOWS)

    runner.downgrade(_PARENT)

    assert _snapshot(engine) == before
    runner.upgrade(_FLOWS)
    assert _tables(engine) >= _NEW_TABLES
