"""Helpers the ``tenant_tighten_migration`` tests share across the revisions they prove."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from tests.migration_support_tenant_keys_migration import (
    seeded_at_the_backfill,
)

KEYS = "20261008_1300_tenant_keys"


def seeded_at_the_keys(tmp_path: Path) -> tuple[str, sa.Engine]:
    """A store holding one row in every tenant-owned table, keyed by its one tenant where the key changes."""
    db_url, engine = seeded_at_the_backfill(tmp_path)
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade(KEYS)
    return db_url, engine


def index_names(engine: sa.Engine, table: str) -> set[str]:
    return {str(index["name"]) for index in sa.inspect(engine).get_indexes(table)}
