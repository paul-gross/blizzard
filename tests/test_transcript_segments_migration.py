"""The transcript-segments migration (blizzard#247, Phase 1 — component tier): applies
from an empty store to head, and survives a downgrade/upgrade roundtrip."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime

pytestmark = pytest.mark.component


def _table_names(db_url: str) -> set[str]:
    engine = create_engine_from_url(db_url)
    try:
        return set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_transcript_tables_exist_after_a_fresh_migration_to_head(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path)  # upgrades an empty store to head
    tables = _table_names(config.db_url)
    assert "transcript_segments" in tables
    assert "transcript_high_water" in tables


def test_transcript_tables_survive_a_downgrade_upgrade_roundtrip(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path)
    runner = hub_runtime.migration_runner(config)

    runner.downgrade("20260803_1000_hub_escalation_wrapped_takeover")
    tables = _table_names(config.db_url)
    assert "transcript_segments" not in tables
    assert "transcript_high_water" not in tables

    runner.upgrade("head")
    tables = _table_names(config.db_url)
    assert "transcript_segments" in tables
    assert "transcript_high_water" in tables
