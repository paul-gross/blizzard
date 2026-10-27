"""Tests proving the ``20261006_0900_config_import_facts`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime
from tests.config_inputs import init_hub

pytestmark = [pytest.mark.unit]

_CONFIG_IMPORT_FACTS_PARENT = "20261005_1000_scope_routine_revisions"


def test_config_import_facts_table_survives_migration_roundtrip(tmp_path: Path) -> None:
    """``config_import_facts`` — downgraded by id below the revision."""
    config = init_hub(tmp_path)
    runner = hub_runtime.migration_runner(config)

    def _table_names() -> set[str]:
        engine = create_engine_from_url(config.db_url)
        try:
            return set(sa.inspect(engine).get_table_names())
        finally:
            engine.dispose()

    assert "config_import_facts" in _table_names()

    runner.downgrade(_CONFIG_IMPORT_FACTS_PARENT)
    assert "config_import_facts" not in _table_names()
    assert "config_changes" in _table_names()

    runner.upgrade("head")
    assert "config_import_facts" in _table_names()
