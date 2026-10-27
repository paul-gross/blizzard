"""Tests proving the ``20260920_1100_usage_harness_provenance`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime
from tests.config_inputs import init_hub

pytestmark = [pytest.mark.unit]


def test_hub_usage_facts_harness_columns_survive_migration_roundtrip(tmp_path: Path) -> None:
    """``usage_facts.harness_id``/``harness_version`` — downgraded by id below the revision."""
    config = init_hub(tmp_path)  # upgrades to head
    runner = hub_runtime.migration_runner(config)

    def _columns() -> set[str]:
        engine = create_engine_from_url(config.db_url)
        try:
            return {c["name"] for c in sa.inspect(engine).get_columns("usage_facts")}
        finally:
            engine.dispose()

    assert {"harness_id", "harness_version"} <= _columns()

    runner.downgrade("20260919_1000_hub_transcript_provenance")
    assert not ({"harness_id", "harness_version"} & _columns())

    runner.upgrade("head")
    assert {"harness_id", "harness_version"} <= _columns()
