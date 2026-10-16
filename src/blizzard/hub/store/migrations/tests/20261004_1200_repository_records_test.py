"""Tests proving the ``20261004_1200_repository_records`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime

pytestmark = [pytest.mark.unit]

_REPOSITORY_RECORDS_PARENT = "20261004_1100_work_source_records"


def test_repository_record_tables_survive_migration_roundtrip(tmp_path: Path) -> None:
    """``repositories``/``repository_lifecycle_facts`` — downgraded to the revision's parent."""
    config = hub_runtime.init_environment(tmp_path)
    runner = hub_runtime.migration_runner(config)
    tables = {"repositories", "repository_lifecycle_facts"}

    def _table_names() -> set[str]:
        engine = create_engine_from_url(config.db_url)
        try:
            return set(sa.inspect(engine).get_table_names())
        finally:
            engine.dispose()

    assert tables <= _table_names()

    runner.downgrade(_REPOSITORY_RECORDS_PARENT)
    assert not tables & _table_names()
    assert {"work_sources", "config_changes"} <= _table_names()

    runner.upgrade("head")
    assert tables <= _table_names()
