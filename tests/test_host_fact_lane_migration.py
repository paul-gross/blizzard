"""The host fact lane revision: three tenant-keyed tables are created, a re-run changes nothing, and the downgrade
restores the store exactly."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store import schema as s
from tests.support import migrate_to
from tests.test_store_migrations import _schema_snapshot

pytestmark = pytest.mark.component

_PARENT = "20261012_1500_work_source_identity"
_LANE = "20261013_0900_host_fact_lane"
_NEW_TABLES = {"host_high_water", "host_external_usage", "host_external_usage_misses"}


def _tables(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names())


def _columns(engine: sa.Engine, table: str) -> dict[str, bool]:
    return {c["name"]: bool(c["nullable"]) for c in sa.inspect(engine).get_columns(table)}


def test_the_upgrade_creates_the_tenant_keyed_tables(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    assert not _tables(engine) & _NEW_TABLES

    runner.upgrade(_LANE)

    assert _tables(engine) >= _NEW_TABLES
    inspector = sa.inspect(engine)
    for table in _NEW_TABLES:
        assert _columns(engine, table)["tenant_id"] is False
        assert f"ix_{table}_tenant_id" in {i["name"] for i in inspector.get_indexes(table)}
        assert {f["name"] for f in inspector.get_foreign_keys(table)} == {f"fk_{table}_tenant_id"}
    assert inspector.get_pk_constraint("host_external_usage")["constrained_columns"] == ["host_id", "slug"]
    assert inspector.get_pk_constraint("host_external_usage_misses")["constrained_columns"] == ["host_id", "slug"]
    assert inspector.get_pk_constraint("host_high_water")["constrained_columns"] == ["host_id"]


def test_the_new_tables_match_the_declared_schema(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    declared = create_engine_from_url(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        s.metadata.create_all(declared)
        for table in _NEW_TABLES:
            assert _columns(engine, table) == _columns(declared, table)
    finally:
        declared.dispose()


def test_a_rerun_over_a_store_already_reshaped_changes_nothing(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _LANE)
    schema = _schema_snapshot(engine, exemptions={}, name_exemptions={})
    with engine.begin() as conn:
        conn.execute(sa.text(f"UPDATE alembic_version SET version_num = '{_PARENT}'"))

    runner.upgrade(_LANE)

    assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == schema


def test_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    before = _schema_snapshot(engine, exemptions={}, name_exemptions={})
    runner.upgrade(_LANE)

    runner.downgrade(_PARENT)

    assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == before
    assert not _tables(engine) & _NEW_TABLES
    runner.upgrade(_LANE)
    assert _tables(engine) >= _NEW_TABLES
