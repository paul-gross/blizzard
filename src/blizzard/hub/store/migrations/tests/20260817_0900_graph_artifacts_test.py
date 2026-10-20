"""Tests proving the ``20260817_0900_graph_artifacts`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime
from tests.config_inputs import init_hub

pytestmark = [pytest.mark.unit]


def test_graph_artifacts_table_survives_migration_roundtrip(tmp_path: Path) -> None:
    """Hub-only (the graph mint's own child table) — downgrades to this revision's own
    parent by id, so the drop half is asserted rather than inferred from a revision
    marker, which a ``downgrade()`` that dropped nothing would satisfy just as well."""
    config = init_hub(tmp_path)  # upgrades to head
    runner = hub_runtime.migration_runner(config)

    def _has_table() -> bool:
        engine = create_engine_from_url(config.db_url)
        try:
            return "graph_artifacts" in sa.inspect(engine).get_table_names()
        finally:
            engine.dispose()

    assert _has_table()

    runner.downgrade("20260812_1300_hub_transcript_events_subject_tool")
    assert not _has_table()

    runner.upgrade("head")
    assert _has_table()
