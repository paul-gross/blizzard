"""Tests proving the ``20260829_0900_findings_and_proposals`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime
from tests.config_inputs import init_hub

pytestmark = [pytest.mark.unit]


def test_findings_and_proposals_tables_survive_migration_roundtrip(tmp_path: Path) -> None:
    """``findings``, ``finding_sets``, ``finding_facts``, ``garden_proposals``,
    ``garden_proposal_findings`` — one hand-written revision mints all
    five; downgrades to its own parent by id, so the drop half is asserted rather than
    inferred from a revision marker."""
    config = init_hub(tmp_path)  # upgrades to head
    runner = hub_runtime.migration_runner(config)
    tables = ("findings", "finding_sets", "finding_facts", "garden_proposals", "garden_proposal_findings")

    def _table_names() -> set[str]:
        engine = create_engine_from_url(config.db_url)
        try:
            return set(sa.inspect(engine).get_table_names())
        finally:
            engine.dispose()

    assert set(tables) <= _table_names()

    runner.downgrade("20260828_1000_scopes_and_routines")
    assert not set(tables) & _table_names()

    runner.upgrade("head")
    assert set(tables) <= _table_names()
