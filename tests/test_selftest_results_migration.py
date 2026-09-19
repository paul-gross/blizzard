"""The selftest-results migration (blizzard#438 — component tier): applies from
an empty store to head, and survives a downgrade/upgrade roundtrip."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner import runtime as runner_runtime

pytestmark = pytest.mark.component


def _table_names(db_url: str) -> set[str]:
    engine = create_engine_from_url(db_url)
    try:
        return set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_selftest_result_table_exists_after_a_fresh_migration_to_head(tmp_path: Path) -> None:
    config = runner_runtime.init_environment(tmp_path)  # upgrades an empty store to head
    tables = _table_names(config.db_url)
    assert "selftest_results" in tables


def test_selftest_result_table_survives_a_downgrade_upgrade_roundtrip(tmp_path: Path) -> None:
    config = runner_runtime.init_environment(tmp_path)
    runner = runner_runtime.migration_runner(config)

    runner.downgrade("20260918_1000_invocation_boundaries")
    tables = _table_names(config.db_url)
    assert "selftest_results" not in tables

    runner.upgrade("head")
    tables = _table_names(config.db_url)
    assert "selftest_results" in tables
