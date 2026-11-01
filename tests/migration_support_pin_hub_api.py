"""Helpers the ``pin_hub_api`` tests share across the revisions they prove."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.store import MIGRATIONS_DIR
from tests.support import hub_scoped_engine

# What a revision that stamps a time reads (``migration_clock``).
MIGRATION_CLOCK = FixedClock(datetime(2026, 7, 12, tzinfo=UTC))


PM_POINTER_SOURCE_REF = "20260716_1512_hub_pm_pointer_source_ref"


def scratch_store(tmp_path: Path, revision: str) -> tuple[MigrationRunner, str]:
    """A scratch hub store migrated from ``base`` up to exactly ``revision``."""
    url = f"sqlite:///{tmp_path}/hub.db"
    runner = MigrationRunner(script_location=MIGRATIONS_DIR, url=url, clock=MIGRATION_CLOCK)
    runner.upgrade(revision)
    return runner, url


def _columns(url: str, table: str) -> set[str]:
    engine = hub_scoped_engine(create_engine_from_url(url))
    try:
        return {c["name"] for c in sa.inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def assert_table_shape_is_frozen(tmp_path: Path, revision: str, table: str, expected: set[str]) -> None:
    """A ``base -> <revision>`` build lands the shape that revision shipped with, not
    head-of-tree ``src/blizzard/hub/store/schema.py``'s — the frozen local ``sa.Table`` literal decision. Also
    pins that pre-rename revisions still speak the old table name (``canon:no-retro``)."""
    _runner, url = scratch_store(tmp_path, revision)

    assert _columns(url, table) == expected
