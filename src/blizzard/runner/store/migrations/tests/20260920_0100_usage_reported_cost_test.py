"""Tests proving the ``20260920_0100_usage_reported_cost`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner import runtime as runner_runtime
from tests.migration_support_store_migrations import (
    assert_frozen_table_lacks_a_later_revisions_columns,
)
from tests.support import upgrade_runner_store_as_registered

pytestmark = [pytest.mark.unit]


def test_usage_facts_reported_cost_backfills_every_historical_row_from_its_cost(tmp_path: Path) -> None:
    """``usage_facts.reported_cost_usd`` starts as a copy of ``cost_usd``: before the
    split every recorded cost *was* the harness's own figure, and a row that predates it
    must not read as though no figure was ever reported. An absent cost stays absent."""
    config = runner_runtime.init_environment(tmp_path)  # upgrades to head
    runner = runner_runtime.migration_runner(config)

    def _rows() -> list[tuple[float | None, float | None]]:
        engine = create_engine_from_url(config.db_url)
        try:
            with engine.connect() as conn:
                return [
                    (row[0], row[1])
                    for row in conn.execute(
                        sa.text("select cost_usd, reported_cost_usd from usage_facts order by id")
                    ).all()
                ]
        finally:
            engine.dispose()

    runner.downgrade("20260919_1400_selftest_results")
    engine = create_engine_from_url(config.db_url)
    try:
        with engine.begin() as conn:
            assert "reported_cost_usd" not in {c["name"] for c in sa.inspect(engine).get_columns("usage_facts")}
            for index, cost in enumerate((2.00, None)):
                conn.execute(
                    sa.text(
                        "insert into usage_facts (lease_id, chunk_id, node_id, epoch, generation, kind, model,"
                        " input_tokens, output_tokens, cache_read_tokens, cache_create_tokens, cost_usd, recorded_at)"
                        " values ('lease_1', 'ch_1', 'nd_1', 1, :generation, 'spawn', 'm', 0, 0, 0, 0, :cost,"
                        " '2026-09-19 12:00:00')"
                    ),
                    {"generation": index + 1, "cost": cost},
                )
    finally:
        engine.dispose()

    upgrade_runner_store_as_registered(runner, config.db_url)
    assert _rows() == [(2.00, 2.00), (None, None)]


@pytest.mark.parametrize(
    "tree,parent_revision,table,columns,direction",
    [
        pytest.param(
            "runner",
            "20260919_1400_selftest_results",
            "usage_facts",
            ("reported_cost_usd",),
            "added",
            id="runner.usage_facts.reported_cost_usd",
        ),
    ],
)
def test_frozen_table_lacks_a_later_revisions_columns(
    tree: str, parent_revision: str, table: str, columns: tuple[str, ...], direction: str, tmp_path: Path
) -> None:
    assert_frozen_table_lacks_a_later_revisions_columns(tree, parent_revision, table, columns, direction, tmp_path)
