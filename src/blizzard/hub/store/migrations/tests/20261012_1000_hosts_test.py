"""The hosts revision: three tenant-keyed tables are created and ``runner_registrations`` gains a nullable ``host_id``
with its key and a tenant-led index, every existing runner standing alone; a re-run changes nothing; and the downgrade
refuses while a runner stands under a host, else restores the store exactly."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store import schema as s
from tests.migration_support_store_migrations import (
    _schema_snapshot,
    assert_frozen_table_lacks_a_later_revisions_columns,
)
from tests.support import default_tenant, migrate_to

_PARENT = "20261012_0900_repository_identity"

_HOSTS = "20261012_1000_hosts"

_AT = datetime(2026, 10, 12, tzinfo=UTC)

_NEW_TABLES = {"hosts", "host_lifecycle_facts", "host_token_revocations"}


def _runner(engine: sa.Engine, tenant: str, runner_id: str = "rn_a") -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO runner_registrations (runner_id, name, added_at, tenant_id)"
                " VALUES (:id, 'a', :at, :tenant)"
            ),
            {"id": runner_id, "at": _AT, "tenant": tenant},
        )


def _host(engine: sa.Engine, tenant: str, host_id: str = "hs_a") -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO hosts (host_id, name, added_at, tenant_id) VALUES (:id, 'box', :at, :tenant)"),
            {"id": host_id, "at": _AT, "tenant": tenant},
        )


def _columns(engine: sa.Engine, table: str) -> dict[str, bool]:
    return {c["name"]: bool(c["nullable"]) for c in sa.inspect(engine).get_columns(table)}


def _tables(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names())


@pytest.mark.component
def test_the_upgrade_creates_the_tables_and_leaves_every_runner_standing_alone(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    tenant = default_tenant(engine)
    _runner(engine, tenant)
    assert not _tables(engine) & _NEW_TABLES

    runner.upgrade(_HOSTS)

    inspector = sa.inspect(engine)
    assert _tables(engine) >= _NEW_TABLES
    columns = {c["name"]: c for c in inspector.get_columns("runner_registrations")}
    assert columns["host_id"]["nullable"] is True
    assert {f["name"] for f in inspector.get_foreign_keys("runner_registrations")} >= {"fk_runner_registrations_hosts"}
    assert "ix_runner_registrations_tenant_host_id" in {
        i["name"] for i in inspector.get_indexes("runner_registrations")
    }
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT host_id FROM runner_registrations")).scalars().all() == [None]
    for table in _NEW_TABLES:
        assert _columns(engine, table)["tenant_id"] is False


@pytest.mark.component
def test_the_new_tables_match_the_declared_schema(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    declared = create_engine_from_url(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        s.metadata.create_all(declared)
        for table in (*_NEW_TABLES, "runner_registrations"):
            assert _tables(engine) >= {table}
            assert _columns(engine, table) == _columns(declared, table)
    finally:
        declared.dispose()


@pytest.mark.component
def test_a_rerun_over_a_store_already_reshaped_changes_nothing(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _HOSTS)
    schema = _schema_snapshot(engine, exemptions={}, name_exemptions={})
    with engine.begin() as conn:
        conn.execute(sa.text(f"UPDATE alembic_version SET version_num = '{_PARENT}'"))

    runner.upgrade(_HOSTS)

    assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == schema


@pytest.mark.component
def test_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    before = _schema_snapshot(engine, exemptions={}, name_exemptions={})
    tenant = default_tenant(engine)
    _runner(engine, tenant)
    runner.upgrade(_HOSTS)

    runner.downgrade(_PARENT)

    assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == before
    assert not _tables(engine) & _NEW_TABLES
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT runner_id FROM runner_registrations")).scalars().all() == ["rn_a"]
    runner.upgrade(_HOSTS)
    assert _tables(engine) >= _NEW_TABLES


@pytest.mark.component
def test_the_downgrade_refuses_while_a_runner_stands_under_a_host(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _HOSTS)
    tenant = default_tenant(engine)
    _host(engine, tenant)
    _runner(engine, tenant, "rn_hosted")
    _runner(engine, tenant, "rn_alone")
    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE runner_registrations SET host_id = 'hs_a' WHERE runner_id = 'rn_hosted'"))

    with pytest.raises(RuntimeError, match="rn_hosted") as refused:
        runner.downgrade(_PARENT)

    assert "rn_alone" not in str(refused.value)
    assert _tables(engine) >= _NEW_TABLES
    assert "host_id" in _columns(engine, "runner_registrations")


@pytest.mark.unit
@pytest.mark.parametrize(
    "tree,parent_revision,table,columns,direction",
    [
        pytest.param(
            "hub",
            "20261012_0900_repository_identity",
            "runner_registrations",
            ("host_id",),
            "added",
            id="hub.runner_registrations.host_id",
        ),
    ],
)
def test_frozen_table_lacks_a_later_revisions_columns(
    tree: str, parent_revision: str, table: str, columns: tuple[str, ...], direction: str, tmp_path: Path
) -> None:
    assert_frozen_table_lacks_a_later_revisions_columns(tree, parent_revision, table, columns, direction, tmp_path)
