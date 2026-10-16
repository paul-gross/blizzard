"""Helpers the ``tenant_tighten_migration`` tests share across the revisions they prove."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from tests.test_tenant_keys_migration import (
    _seeded_at_the_backfill,
)

_KEYS = "20261008_1300_tenant_keys"


def _seeded_at_the_keys(tmp_path: Path) -> tuple[str, sa.Engine]:
    """A store holding one row in every tenant-owned table, keyed by its one tenant where the key changes."""
    db_url, engine = _seeded_at_the_backfill(tmp_path)
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade(_KEYS)
    return db_url, engine


def _index_names(engine: sa.Engine, table: str) -> set[str]:
    return {str(index["name"]) for index in sa.inspect(engine).get_indexes(table)}
