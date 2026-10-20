"""Tests proving the ``20260918_1000_invocation_boundaries`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner import runtime as runner_runtime
from tests.config_inputs import init_host

pytestmark = [pytest.mark.unit]


def test_invocation_boundaries_table_survives_migration_roundtrip(tmp_path: Path) -> None:
    """The transcript invocation-boundary ledger — downgrades to this
    revision's own parent by id, so the drop half is asserted rather than inferred from a
    revision marker, which a ``downgrade()`` that dropped nothing would satisfy just as well."""
    config = init_host(tmp_path)  # upgrades to head
    runner = runner_runtime.migration_runner(config)

    def _has_table() -> bool:
        engine = create_engine_from_url(config.db_url)
        try:
            return "invocation_boundaries" in sa.inspect(engine).get_table_names()
        finally:
            engine.dispose()

    assert _has_table()

    runner.downgrade("20260917_1200_elicitation_process_group")
    assert not _has_table()

    runner.upgrade("head")
    assert _has_table()
