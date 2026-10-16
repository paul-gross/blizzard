"""Helpers the ``tenant_keys_migration`` tests share across the revisions they prove."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from tests.migration_support_store_migrations import _schema_snapshot
from tests.migration_support_tenant_carry_over_migration import _seed_every_tenant_owned_table

_BEFORE_TENANTS = "20261008_1000_drop_work_item_proposals"


_BACKFILL = "20261008_1200_tenant_id_backfill"


_T0 = datetime(2026, 1, 1, tzinfo=UTC)


_SECOND_TENANT = "ten_01J9Z3M0P8QK7V2S4W6X8Y0A1B"


def _store(tmp_path: Path) -> tuple[str, sa.Engine]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    return db_url, create_engine_from_url(db_url)


def _seeded_at_the_backfill(tmp_path: Path) -> tuple[str, sa.Engine]:
    """A store holding one row in every tenant-owned table, carried into the one tenant by the backfill."""
    db_url, engine = _store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE_TENANTS)
    _seed_every_tenant_owned_table(engine)
    runner.upgrade(_BACKFILL)
    return db_url, engine


def _rows(engine: sa.Engine) -> dict[str, list[str]]:
    """Every row of every table, as comparable text, in a stable order."""
    with engine.connect() as conn:
        return {
            table: sorted(
                repr(tuple(row)) for row in conn.execute(sa.select(sa.text("*")).select_from(sa.table(table)))
            )
            for table in sa.inspect(engine).get_table_names()
            if table != "alembic_version"
        }


def _snapshot(engine: sa.Engine) -> dict[str, object]:
    return _schema_snapshot(engine, exemptions={}, name_exemptions={})


def _tenant_ids(engine: sa.Engine) -> list[str]:
    with engine.connect() as conn:
        return list(conn.execute(sa.text("SELECT tenant_id FROM tenants")).scalars())


def _tenant_column_nullable(engine: sa.Engine, table: str) -> bool:
    return next(c["nullable"] for c in sa.inspect(engine).get_columns(table) if c["name"] == "tenant_id")
