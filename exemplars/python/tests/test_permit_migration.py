"""The permits revision: two tenant-keyed tables, permits keyed to their project, are created as the schema declares
them, a re-run changes nothing, and the downgrade restores the store exactly."""

# exemplar: bzh:manual-migrations. The revision sits outside the hub's chain, so ``_apply`` drives it on a store the
# real chain migrated to its parent; a production test calls ``runner.upgrade``/``runner.downgrade``.

from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from blizzard.foundation.store.engine import create_engine_from_url
from exemplar.hub.store import schema as s
from tests.support import migrate_to
from tests.test_store_migrations import _schema_snapshot

pytestmark = pytest.mark.component

_PERMITS = importlib.import_module("exemplar.hub.store.migrations.versions.20261015_0900_permits")
_PARENT = _PERMITS.down_revision
_NEW_TABLES = {"permits", "permit_facts"}


def _apply(engine: sa.Engine, step: Callable[[], None]) -> None:
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        step()


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
    return _schema_snapshot(engine, exemptions={}, name_exemptions={})


def test_the_upgrade_creates_the_project_keyed_tables(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, _PARENT)
    assert not _tables(engine) & _NEW_TABLES

    _apply(engine, _PERMITS.upgrade)

    inspector = sa.inspect(engine)
    assert inspector.get_pk_constraint("permits")["constrained_columns"] == ["permit_id"]
    assert {(u["name"], tuple(u["column_names"])) for u in inspector.get_unique_constraints("permits")} == {
        ("uq_permits_tenant_project_holder_capability", ("tenant_id", "project_id", "holder", "capability"))
    }
    assert {(f["name"], f["referred_table"]) for f in inspector.get_foreign_keys("permits")} == {
        ("fk_permits_tenant_id", "tenants"),
        ("fk_permits_projects", "projects"),
    }
    assert {i["name"] for i in inspector.get_indexes("permits")} == {
        "ix_permits_tenant_requested_at_permit_id",
        "ix_permits_tenant_project_requested_at_permit_id",
        "ix_permits_tenant_holder_requested_at_permit_id",
    }
    assert {f["name"] for f in inspector.get_foreign_keys("permit_facts")} == {
        "fk_permit_facts_tenant_id",
        "fk_permit_facts_permits",
    }


def test_the_new_tables_match_the_declared_schema(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, _PARENT)
    _apply(engine, _PERMITS.upgrade)
    declared = create_engine_from_url(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        s.metadata.create_all(declared)
        for table in _NEW_TABLES:
            assert _shape(engine, table) == _shape(declared, table)
    finally:
        declared.dispose()


def test_a_rerun_over_a_store_already_reshaped_changes_nothing(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, _PARENT)
    _apply(engine, _PERMITS.upgrade)
    schema = _snapshot(engine)

    _apply(engine, _PERMITS.upgrade)

    assert _snapshot(engine) == schema


def test_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, _PARENT)
    before = _snapshot(engine)
    _apply(engine, _PERMITS.upgrade)

    _apply(engine, _PERMITS.downgrade)

    assert _snapshot(engine) == before
    _apply(engine, _PERMITS.upgrade)
    assert _tables(engine) >= _NEW_TABLES
