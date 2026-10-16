"""Tests proving the ``20261004_1100_runner_lease_closure_escalation_cause`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner import runtime as runner_runtime

pytestmark = [pytest.mark.unit]


def test_runner_lease_closure_escalation_cause_survives_migration_roundtrip(tmp_path: Path) -> None:
    """``lease_closures.escalation_cause`` — downgrades to its own parent by id, so the drop
    half is asserted rather than inferred from a revision marker."""
    config = runner_runtime.init_environment(tmp_path)  # upgrades to head
    runner = runner_runtime.migration_runner(config)

    def _has_column() -> bool:
        engine = create_engine_from_url(config.db_url)
        try:
            return "escalation_cause" in {c["name"] for c in sa.inspect(engine).get_columns("lease_closures")}
        finally:
            engine.dispose()

    assert _has_column()

    runner.downgrade("20261004_1000_runner_invocation_boundary_advances")
    assert not _has_column()

    runner.upgrade("head")
    assert _has_column()
