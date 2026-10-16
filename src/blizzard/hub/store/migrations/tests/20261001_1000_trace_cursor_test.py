"""Tests proving the ``20261001_1000_trace_cursor`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime

pytestmark = [pytest.mark.unit]


def test_trace_cursor_table_and_epoch_owner_index_survive_migration_roundtrip(tmp_path: Path) -> None:
    """Hub-only — downgrades to the revision's own parent, so both the dropped table and the
    dropped ``epoch_owners`` index are asserted rather than inferred from a revision marker."""
    config = hub_runtime.init_environment(tmp_path)  # upgrades to head
    runner = hub_runtime.migration_runner(config)

    def _shape() -> tuple[bool, bool]:
        engine = create_engine_from_url(config.db_url)
        try:
            inspector = sa.inspect(engine)
            owner_indexes = {i["name"] for i in inspector.get_indexes("epoch_owners")}
            return "trace_cursor" in inspector.get_table_names(), "ix_epoch_owners_recorded_at_id" in owner_indexes
        finally:
            engine.dispose()

    assert _shape() == (True, True)

    runner.downgrade("20260930_1000_epoch_owners")
    assert _shape() == (False, False)

    runner.upgrade("head")
    assert _shape() == (True, True)
