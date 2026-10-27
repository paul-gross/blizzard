"""Tests proving the ``20260819_1100_work_items`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime
from tests.config_inputs import init_hub

pytestmark = [pytest.mark.unit]


def test_work_items_tables_survive_migration_roundtrip(tmp_path: Path) -> None:
    """``work_items`` + ``work_item_sequence`` — downgraded by id below the revision."""
    config = init_hub(tmp_path)  # upgrades to head
    runner = hub_runtime.migration_runner(config)

    def _has_tables() -> bool:
        engine = create_engine_from_url(config.db_url)
        try:
            names = sa.inspect(engine).get_table_names()
            return "work_items" in names and "work_item_sequence" in names
        finally:
            engine.dispose()

    assert _has_tables()

    runner.downgrade("20260819_0900_chunk_restarts")
    assert not _has_tables()

    runner.upgrade("head")
    assert _has_tables()
