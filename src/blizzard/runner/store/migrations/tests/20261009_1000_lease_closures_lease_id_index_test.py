"""Tests proving the ``20261009_1000_lease_closures_lease_id_index`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner import runtime as runner_runtime
from tests.config_inputs import init_host

pytestmark = [pytest.mark.component]


def test_lease_closures_lease_id_index_migration_creates_and_drops_the_index_idempotently(tmp_path: Path) -> None:
    """``20261009_1000_runner_lease_closures_lease_id_index``: up creates the index, down drops it, up restores it."""
    config = init_host(tmp_path)
    migration = runner_runtime.migration_runner(config)
    engine = create_engine_from_url(config.db_url)

    def indexes() -> set[str]:
        return {str(i["name"]) for i in sa.inspect(engine).get_indexes("lease_closures")}

    try:
        assert "ix_lease_closures_lease_id_id" in indexes()
        migration.downgrade("20261008_1100_runner_hub_projects")
        assert "ix_lease_closures_lease_id_id" not in indexes()
        migration.upgrade("head")
        assert "ix_lease_closures_lease_id_id" in indexes()
    finally:
        engine.dispose()
