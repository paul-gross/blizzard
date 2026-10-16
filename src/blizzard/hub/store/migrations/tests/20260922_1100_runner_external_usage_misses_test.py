"""Tests proving the ``20260922_1100_runner_external_usage_misses`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime

pytestmark = [pytest.mark.unit]


def test_runner_external_usage_misses_table_survives_migration_roundtrip(tmp_path: Path) -> None:
    """``runner_external_usage_misses`` — one hand-written revision mints
    the table; downgrades to its own parent by id, so the drop half is asserted rather than
    inferred from a revision marker a no-op ``downgrade()`` would satisfy just as well."""
    config = hub_runtime.init_environment(tmp_path)  # upgrades to head
    runner = hub_runtime.migration_runner(config)

    def _has_table() -> bool:
        engine = create_engine_from_url(config.db_url)
        try:
            return "runner_external_usage_misses" in sa.inspect(engine).get_table_names()
        finally:
            engine.dispose()

    assert _has_table()

    runner.downgrade("20260922_1000_review_findings")
    assert not _has_table()

    runner.upgrade("head")
    assert _has_table()
