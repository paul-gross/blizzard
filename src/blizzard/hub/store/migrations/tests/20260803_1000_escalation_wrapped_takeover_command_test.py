"""Tests proving the ``20260803_1000_escalation_wrapped_takeover_command`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime
from tests.config_inputs import init_hub
from tests.migration_support_store_migrations import (
    assert_frozen_table_lacks_a_later_revisions_columns,
)

pytestmark = [pytest.mark.unit]


def test_wrapped_takeover_command_column_survives_migration_roundtrip(tmp_path: Path) -> None:
    """Hub-only (``escalations.wrapped_takeover_command`` has no runner counterpart) —
    downgrades to this revision's own parent by id, not ``"-1"``, so a future revision
    landing between them can't silently break this test's premise."""
    config = init_hub(tmp_path)  # upgrades to head
    runner = hub_runtime.migration_runner(config)

    def _has_column() -> bool:
        engine = create_engine_from_url(config.db_url)
        try:
            columns = {c["name"] for c in sa.inspect(engine).get_columns("escalations")}
        finally:
            engine.dispose()
        return "wrapped_takeover_command" in columns

    assert _has_column()

    runner.downgrade("20260801_1600_hub_runner_external_usage")
    assert not _has_column()

    runner.upgrade("head")
    assert _has_column()


@pytest.mark.parametrize(
    "tree,parent_revision,table,columns,direction",
    [
        pytest.param(
            "hub",
            "20260801_1600_hub_runner_external_usage",
            "escalations",
            ("wrapped_takeover_command",),
            "added",
            id="hub.escalations.wrapped_takeover_command",
        ),
    ],
)
def test_frozen_table_lacks_a_later_revisions_columns(
    tree: str, parent_revision: str, table: str, columns: tuple[str, ...], direction: str, tmp_path: Path
) -> None:
    assert_frozen_table_lacks_a_later_revisions_columns(tree, parent_revision, table, columns, direction, tmp_path)
