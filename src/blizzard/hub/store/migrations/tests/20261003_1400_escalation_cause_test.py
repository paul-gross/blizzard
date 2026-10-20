"""Tests proving the ``20261003_1400_escalation_cause`` revision."""

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


def test_escalation_cause_columns_survive_migration_roundtrip(tmp_path: Path) -> None:
    """A pre-cause escalation row reads back with no cause or detail: no backfill."""
    config = init_hub(tmp_path)
    runner = hub_runtime.migration_runner(config)

    def _columns() -> set[str]:
        engine = create_engine_from_url(config.db_url)
        try:
            return {c["name"] for c in sa.inspect(engine).get_columns("escalations")} & {"cause", "detail"}
        finally:
            engine.dispose()

    assert _columns() == {"cause", "detail"}

    runner.downgrade("20261003_1300_hub_transcript_segment_spawn_cwd")
    assert _columns() == set()
    engine = create_engine_from_url(config.db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO escalations (chunk_id, epoch, takeover_command, recorded_at) "
                    "VALUES ('ch_old', 1, '', '2026-10-01T00:00:00+00:00')"
                )
            )
    finally:
        engine.dispose()

    runner.upgrade("head")
    assert _columns() == {"cause", "detail"}
    engine = create_engine_from_url(config.db_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(sa.text("SELECT cause, detail FROM escalations WHERE chunk_id = 'ch_old'")).one()
    finally:
        engine.dispose()
    assert tuple(row) == (None, None)


@pytest.mark.parametrize(
    "tree,parent_revision,table,columns,direction",
    [
        pytest.param(
            "hub",
            "20261003_1300_hub_transcript_segment_spawn_cwd",
            "escalations",
            ("cause", "detail"),
            "added",
            id="hub.escalations.cause",
        ),
    ],
)
def test_frozen_table_lacks_a_later_revisions_columns(
    tree: str, parent_revision: str, table: str, columns: tuple[str, ...], direction: str, tmp_path: Path
) -> None:
    assert_frozen_table_lacks_a_later_revisions_columns(tree, parent_revision, table, columns, direction, tmp_path)
