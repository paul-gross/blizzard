"""Tests proving the ``20260831_0945_garden_proposal_closures`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime
from tests.config_inputs import init_hub

pytestmark = [pytest.mark.unit]


def test_garden_proposal_closures_table_survives_migration_roundtrip(tmp_path: Path) -> None:
    """``garden_proposal_closures`` — one hand-written revision mints the
    table; downgrades to its own parent by id, so the drop half is asserted rather than
    inferred from a revision marker."""
    config = init_hub(tmp_path)  # upgrades to head
    runner = hub_runtime.migration_runner(config)

    def _table_names() -> set[str]:
        engine = create_engine_from_url(config.db_url)
        try:
            return set(sa.inspect(engine).get_table_names())
        finally:
            engine.dispose()

    assert "garden_proposal_closures" in _table_names()

    runner.downgrade("20260830_2015_garden_proposals_source_artifact")
    assert "garden_proposal_closures" not in _table_names()

    runner.upgrade("head")
    assert "garden_proposal_closures" in _table_names()
