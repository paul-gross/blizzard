"""Tests proving the ``20260902_0900_chunk_dependencies`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime

pytestmark = [pytest.mark.unit]


def test_chunk_dependencies_table_survives_migration_roundtrip(tmp_path: Path) -> None:
    """``chunk_dependencies`` — one hand-written revision mints the table;
    downgrades to its own parent by id, so the drop half is asserted rather than inferred
    from a revision marker a no-op ``downgrade()`` would satisfy just as well."""
    config = hub_runtime.init_environment(tmp_path)  # upgrades to head
    runner = hub_runtime.migration_runner(config)

    def _table_names() -> set[str]:
        engine = create_engine_from_url(config.db_url)
        try:
            return set(sa.inspect(engine).get_table_names())
        finally:
            engine.dispose()

    assert "chunk_dependencies" in _table_names()

    runner.downgrade("20260901_0900_finding_facts_finding_set_id")
    assert "chunk_dependencies" not in _table_names()

    runner.upgrade("head")
    assert "chunk_dependencies" in _table_names()
