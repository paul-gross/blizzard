"""Tests proving the ``20261003_0900_egress_cursor`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime
from tests.config_inputs import init_hub

pytestmark = [pytest.mark.unit]


def test_egress_cursor_table_and_usage_index_survive_migration_roundtrip(tmp_path: Path) -> None:
    """Hub-only — the table and the ``usage_facts`` index, downgraded by id below the revision."""
    config = init_hub(tmp_path)  # upgrades to head
    runner = hub_runtime.migration_runner(config)

    def _shape() -> tuple[bool, bool]:
        engine = create_engine_from_url(config.db_url)
        try:
            inspector = sa.inspect(engine)
            usage_indexes = {i["name"] for i in inspector.get_indexes("usage_facts")}
            return "egress_cursor" in inspector.get_table_names(), "ix_usage_facts_recorded_at_id" in usage_indexes
        finally:
            engine.dispose()

    assert _shape() == (True, True)

    runner.downgrade("20261001_1000_trace_cursor")
    assert _shape() == (False, False)

    runner.upgrade("head")
    assert _shape() == (True, True)
