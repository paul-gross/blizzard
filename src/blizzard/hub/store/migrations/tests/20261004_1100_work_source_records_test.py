"""Tests proving the ``20261004_1100_work_source_records`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime
from tests.config_inputs import init_hub

pytestmark = [pytest.mark.unit]

_WORK_SOURCE_RECORDS_PARENT = "20261004_1000_hub_secrets"


def test_work_source_record_tables_survive_migration_roundtrip(tmp_path: Path) -> None:
    """``work_sources``/``work_source_lifecycle_facts``/``config_changes`` — downgraded by id below the revision."""
    config = init_hub(tmp_path)
    runner = hub_runtime.migration_runner(config)
    tables = {"work_sources", "work_source_lifecycle_facts", "config_changes"}

    def _table_names() -> set[str]:
        engine = create_engine_from_url(config.db_url)
        try:
            return set(sa.inspect(engine).get_table_names())
        finally:
            engine.dispose()

    assert tables <= _table_names()

    runner.downgrade(_WORK_SOURCE_RECORDS_PARENT)
    assert not tables & _table_names()
    assert "secrets" in _table_names()

    runner.upgrade("head")
    assert tables <= _table_names()
