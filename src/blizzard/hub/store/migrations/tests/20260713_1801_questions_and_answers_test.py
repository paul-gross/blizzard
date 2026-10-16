"""Tests proving the ``20260713_1801_questions_and_answers`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime
from tests.support import hub_scoped_engine

pytestmark = [pytest.mark.component]


def test_questions_historical_revision_does_not_create_later_harness_provenance(tmp_path: Path) -> None:
    """The later provenance migration must see a column to add and backfill itself."""
    config = hub_runtime.init_environment(tmp_path)
    runner = hub_runtime.migration_runner(config)
    runner.downgrade("20260713_1716_hub_graph_node_produces_checks")

    runner.upgrade("20260713_1801_hub_questions_and_answers")
    engine = hub_scoped_engine(create_engine_from_url(config.db_url))
    try:
        columns = {column["name"] for column in sa.inspect(engine).get_columns("questions")}
    finally:
        engine.dispose()

    assert "harness_id" not in columns
