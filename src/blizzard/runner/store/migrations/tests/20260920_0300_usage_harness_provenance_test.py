"""Tests proving the ``20260920_0300_usage_harness_provenance`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner import runtime as runner_runtime

pytestmark = [pytest.mark.unit]


def test_runner_usage_facts_harness_columns_survive_migration_roundtrip(tmp_path: Path) -> None:
    """``usage_facts.harness_id``/``harness_version``, the runner's own
    durable row — downgrades to this revision's own parent by id, so the drop half is
    asserted rather than inferred from a revision marker."""
    config = runner_runtime.init_environment(tmp_path)  # upgrades to head
    runner = runner_runtime.migration_runner(config)

    def _columns() -> set[str]:
        engine = create_engine_from_url(config.db_url)
        try:
            return {c["name"] for c in sa.inspect(engine).get_columns("usage_facts")}
        finally:
            engine.dispose()

    assert {"harness_id", "harness_version"} <= _columns()

    runner.downgrade("20260920_0200_usage_cost_is_share")
    assert not ({"harness_id", "harness_version"} & _columns())

    runner.upgrade("head")
    assert {"harness_id", "harness_version"} <= _columns()
