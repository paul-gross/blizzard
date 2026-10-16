"""Tests proving the ``20260831_0900_work_items_routine_run`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime

pytestmark = [pytest.mark.unit]


def test_work_items_routine_run_columns_survive_migration_roundtrip(tmp_path: Path) -> None:
    """``work_items.routine_name``/``scope_slug``/``run_mode`` —
    downgrades to this revision's own parent by id, so the drop half is asserted rather
    than inferred from a revision marker a no-op ``downgrade()`` would satisfy just as
    well."""
    config = hub_runtime.init_environment(tmp_path)  # upgrades to head
    runner = hub_runtime.migration_runner(config)
    columns = ("routine_name", "scope_slug", "run_mode")

    def _columns() -> set[str]:
        engine = create_engine_from_url(config.db_url)
        try:
            return {c["name"] for c in sa.inspect(engine).get_columns("work_items")}
        finally:
            engine.dispose()

    assert set(columns) <= _columns()

    runner.downgrade("20260830_2015_garden_proposals_source_artifact")
    assert not set(columns) & _columns()

    runner.upgrade("head")
    assert set(columns) <= _columns()
