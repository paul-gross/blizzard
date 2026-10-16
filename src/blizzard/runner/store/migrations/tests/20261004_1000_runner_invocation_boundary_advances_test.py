"""Tests proving the ``20261004_1000_runner_invocation_boundary_advances`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner import runtime as runner_runtime

pytestmark = [pytest.mark.unit]


def test_runner_invocation_boundary_advances_table_survives_migration_roundtrip(tmp_path: Path) -> None:
    """``invocation_boundary_advances`` — downgrades to its own parent by id, so the drop half
    is asserted rather than inferred from a revision marker."""
    config = runner_runtime.init_environment(tmp_path)  # upgrades to head
    runner = runner_runtime.migration_runner(config)

    def _shape() -> tuple[bool, bool]:
        engine = create_engine_from_url(config.db_url)
        try:
            inspector = sa.inspect(engine)
            if "invocation_boundary_advances" not in inspector.get_table_names():
                return False, False
            indexes = {i["name"] for i in inspector.get_indexes("invocation_boundary_advances")}
            return True, "ix_invocation_boundary_advances_boundary" in indexes
        finally:
            engine.dispose()

    assert _shape() == (True, True)

    runner.downgrade("20261004_1000_binding_release_epoch_floor")
    assert _shape() == (False, False)

    runner.upgrade("head")
    assert _shape() == (True, True)
