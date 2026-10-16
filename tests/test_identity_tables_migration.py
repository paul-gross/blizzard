"""The identity tables revision: ``membership_facts`` and ``hub_admin_facts`` are created exactly as the schema
declares them, with their named keys and indexes; a re-run over a store that already holds one of them creates only
what is missing; and the downgrade, with one tenant only, drops both and restores the store exactly."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema as s
from tests.test_store_migrations import _schema_snapshot

pytestmark = [pytest.mark.component, pytest.mark.migration_revision("20261008_1650_identity_tables")]

_MOVE = "20261008_1600_hub_event_log_move"
_IDENTITY = "20261008_1650_identity_tables"
_SECOND_TENANT = "ten_01J9Z3M0P8QK7V2S4W6X8Y0A1C"
_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_TABLES = ("membership_facts", "hub_admin_facts")


@contextmanager
def _at_the_move(tmp_path: Path) -> Iterator[tuple[MigrationRunner, sa.Engine]]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_MOVE)
    engine = create_engine_from_url(db_url)
    try:
        yield runner, engine
    finally:
        engine.dispose()


def _tables(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names())


def _declared(table: sa.Table) -> dict[str, object]:
    return {
        "columns": {(column.name, column.nullable) for column in table.columns},
        "foreign_keys": {
            (str(key.name), tuple(c.name for c in key.columns), key.referred_table.name)
            for key in table.foreign_key_constraints
        },
        "indexes": {(str(index.name), tuple(c.name for c in index.columns)) for index in table.indexes},
    }


def _migrated(engine: sa.Engine, name: str) -> dict[str, object]:
    inspector = sa.inspect(engine)
    return {
        "columns": {(column["name"], column["nullable"]) for column in inspector.get_columns(name)},
        "foreign_keys": {
            (str(key["name"]), tuple(key["constrained_columns"]), key["referred_table"])
            for key in inspector.get_foreign_keys(name)
        },
        "indexes": {(str(index["name"]), tuple(index["column_names"])) for index in inspector.get_indexes(name)},
    }


def test_the_upgrade_creates_both_tables_as_the_schema_declares_them(tmp_path: Path) -> None:
    with _at_the_move(tmp_path) as (runner, engine):
        assert not _tables(engine) & set(_TABLES)

        runner.upgrade(_IDENTITY)

        assert _migrated(engine, "membership_facts") == _declared(s.membership_facts)
        assert _migrated(engine, "hub_admin_facts") == _declared(s.hub_admin_facts)


def test_a_rerun_over_a_store_holding_one_table_creates_only_what_is_missing(tmp_path: Path) -> None:
    with _at_the_move(tmp_path) as (runner, engine):
        with engine.begin() as conn:
            s.hub_admin_facts.create(conn)
            conn.execute(sa.text("DROP INDEX ix_hub_admin_facts_user_id"))
            conn.execute(
                sa.insert(s.hub_admin_facts).values(user_id="usr_1", granted=True, set_at=_T0, set_by="bootstrap")
            )

        runner.upgrade(_IDENTITY)

        assert _migrated(engine, "membership_facts") == _declared(s.membership_facts)
        assert _migrated(engine, "hub_admin_facts") == _declared(s.hub_admin_facts)
        with engine.connect() as conn:
            assert conn.execute(sa.select(s.hub_admin_facts.c.user_id)).scalars().all() == ["usr_1"]


def test_the_downgrade_refuses_while_a_second_tenant_exists_naming_both(tmp_path: Path) -> None:
    with _at_the_move(tmp_path) as (runner, engine):
        runner.upgrade(_IDENTITY)
        with engine.begin() as conn:
            [tenant] = conn.execute(sa.text("SELECT tenant_id FROM tenants")).scalars().all()
            conn.execute(
                sa.text(
                    "INSERT INTO tenants (tenant_id, name, created_at, created_by) VALUES (:t, 'other', :at, 'usr_1')"
                ),
                {"t": _SECOND_TENANT, "at": _T0},
            )

        with pytest.raises(RuntimeError, match="delete every tenant but one before downgrading") as refused:
            runner.downgrade(_MOVE)

        assert tenant in str(refused.value) and _SECOND_TENANT in str(refused.value)
        assert set(_TABLES) <= _tables(engine)


def test_with_one_tenant_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    with _at_the_move(tmp_path) as (runner, engine):
        schema = _schema_snapshot(engine, exemptions={}, name_exemptions={})

        runner.upgrade(_IDENTITY)
        with engine.begin() as conn:
            [tenant] = conn.execute(sa.text("SELECT tenant_id FROM tenants")).scalars().all()
            conn.execute(
                sa.insert(s.membership_facts).values(
                    tenant_id=tenant, user_id="usr_1", role="admin", set_at=_T0, set_by="bootstrap"
                )
            )
        runner.downgrade(_MOVE)

        assert not _tables(engine) & set(_TABLES)
        assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == schema
