"""Tests proving the ``20260922_1200_hub_usage_estimated_cost`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime
from tests.config_inputs import init_hub

pytestmark = [pytest.mark.unit]


def test_hub_usage_facts_estimated_cost_column_survives_migration_roundtrip(tmp_path: Path) -> None:
    """``usage_facts.estimated_cost_usd`` — downgrades to this revision's own parent by
    id, so the drop half is asserted rather than inferred from a revision marker, which a
    ``downgrade()`` that dropped nothing would satisfy just as well."""
    config = init_hub(tmp_path)  # upgrades to head
    runner = hub_runtime.migration_runner(config)

    def _columns() -> set[str]:
        engine = create_engine_from_url(config.db_url)
        try:
            return {c["name"] for c in sa.inspect(engine).get_columns("usage_facts")}
        finally:
            engine.dispose()

    assert "estimated_cost_usd" in _columns()

    runner.downgrade("20260922_1100_runner_external_usage_misses")
    assert "estimated_cost_usd" not in _columns()

    runner.upgrade("head")
    assert "estimated_cost_usd" in _columns()
