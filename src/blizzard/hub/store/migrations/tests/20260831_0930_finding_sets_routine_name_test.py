"""Tests proving the ``20260831_0930_finding_sets_routine_name`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime
from tests.config_inputs import init_hub

pytestmark = [pytest.mark.unit]


def test_finding_sets_routine_name_column_survives_migration_roundtrip(tmp_path: Path) -> None:
    """``finding_sets.routine_name`` — downgrades to this revision's own
    parent by id, so the drop half is asserted rather than inferred from a revision
    marker a no-op ``downgrade()`` would satisfy just as well."""
    config = init_hub(tmp_path)  # upgrades to head
    runner = hub_runtime.migration_runner(config)

    def _has_column() -> bool:
        engine = create_engine_from_url(config.db_url)
        try:
            columns = {c["name"] for c in sa.inspect(engine).get_columns("finding_sets")}
        finally:
            engine.dispose()
        return "routine_name" in columns

    assert _has_column()

    runner.downgrade("20260831_0900_work_items_routine_run")
    assert not _has_column()

    runner.upgrade("head")
    assert _has_column()
