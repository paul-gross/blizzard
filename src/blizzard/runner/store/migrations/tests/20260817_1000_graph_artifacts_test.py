"""Tests proving the ``20260817_1000_graph_artifacts`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner import runtime as runner_runtime
from tests.config_inputs import init_host

pytestmark = [pytest.mark.unit]


def test_runner_graph_artifacts_table_survives_migration_roundtrip(tmp_path: Path) -> None:
    """The runner's own graph-artifact mirror table — downgrades to this
    revision's own parent by id, so the drop half is asserted rather than inferred from a
    revision marker, which a ``downgrade()`` that dropped nothing would satisfy just as well."""
    config = init_host(tmp_path)  # upgrades to head
    runner = runner_runtime.migration_runner(config)

    def _has_table() -> bool:
        engine = create_engine_from_url(config.db_url)
        try:
            return "graph_artifacts" in sa.inspect(engine).get_table_names()
        finally:
            engine.dispose()

    assert _has_table()

    runner.downgrade("20260816_1100_runner_transcript_agent_tool_use_ids")
    assert not _has_table()

    runner.upgrade("head")
    assert _has_table()
