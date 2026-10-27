"""Tests proving the ``20260828_1000_scopes_and_routines`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime
from tests.config_inputs import init_hub

pytestmark = [pytest.mark.unit]


def test_scopes_and_routines_tables_survive_migration_roundtrip(tmp_path: Path) -> None:
    """``scopes``, ``scope_lifecycle_facts``, ``routines`` — one hand-written revision mints all three; downgraded by
    id below the revision."""
    config = init_hub(tmp_path)  # upgrades to head
    runner = hub_runtime.migration_runner(config)
    tables = ("scopes", "scope_lifecycle_facts", "routines")

    def _table_names() -> set[str]:
        engine = create_engine_from_url(config.db_url)
        try:
            return set(sa.inspect(engine).get_table_names())
        finally:
            engine.dispose()

    assert set(tables) <= _table_names()

    runner.downgrade("20260826_0930_close_intents_backfill")
    assert not set(tables) & _table_names()

    runner.upgrade("head")
    assert set(tables) <= _table_names()
