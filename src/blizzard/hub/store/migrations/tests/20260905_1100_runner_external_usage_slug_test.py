"""Tests proving the ``20260905_1100_runner_external_usage_slug`` revision."""

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


def test_runner_external_usage_slug_widens_the_primary_key_and_backfills_the_legacy_row(tmp_path: Path) -> None:
    """``runner_external_usage``'s join key backfills a pre-existing
    row to the legacy slug/name, widens the primary key to ``(runner_id, slug)``, and
    downgrading past it restores the single-column key."""
    config = init_hub(tmp_path)  # upgrades to head
    runner = hub_runtime.migration_runner(config)

    def _pk() -> set[str]:
        engine = create_engine_from_url(config.db_url)
        try:
            return set(sa.inspect(engine).get_pk_constraint("runner_external_usage")["constrained_columns"])
        finally:
            engine.dispose()

    def _columns() -> set[str]:
        engine = create_engine_from_url(config.db_url)
        try:
            return {c["name"] for c in sa.inspect(engine).get_columns("runner_external_usage")}
        finally:
            engine.dispose()

    def _rows() -> list[tuple[str, str | None]]:
        engine = create_engine_from_url(config.db_url)
        try:
            with engine.connect() as conn:
                cols = "runner_id, slug, name" if "slug" in _columns() else "runner_id"
                return [tuple(row) for row in conn.execute(sa.text(f"select {cols} from runner_external_usage")).all()]
        finally:
            engine.dispose()

    # The pre-slug shape — this reshape's own parent, frozen by 20260801_1600.
    runner.downgrade("20260801_1600_hub_runner_external_usage")
    assert _pk() == {"runner_id"}
    assert "slug" not in _columns()
    engine = create_engine_from_url(config.db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "insert into runner_external_usage (runner_id, sampled_at, windows, updated_at) "
                    "values (:runner_id, :sampled_at, :windows, :updated_at)"
                ),
                {
                    "runner_id": "r1",
                    "sampled_at": "2026-08-01 12:00:00",
                    "windows": "[]",
                    "updated_at": "2026-08-01 12:00:00",
                },
            )
    finally:
        engine.dispose()

    runner.upgrade("head")
    assert _pk() == {"runner_id", "slug"}
    assert "name" in _columns()
    assert _rows() == [("r1", "anthropic", "Anthropic")]

    runner.downgrade("20260801_1600_hub_runner_external_usage")
    assert _pk() == {"runner_id"}
    assert "slug" not in _columns()

    runner.upgrade("head")
    assert _pk() == {"runner_id", "slug"}
    assert "name" in _columns()


@pytest.mark.parametrize(
    "tree,parent_revision,table,columns,direction",
    [
        pytest.param(
            "hub",
            "20260801_1600_hub_runner_external_usage",
            "runner_external_usage",
            ("slug", "name"),
            "added",
            id="hub.runner_external_usage.slug",
        ),
    ],
)
def test_frozen_table_lacks_a_later_revisions_columns(
    tree: str, parent_revision: str, table: str, columns: tuple[str, ...], direction: str, tmp_path: Path
) -> None:
    assert_frozen_table_lacks_a_later_revisions_columns(tree, parent_revision, table, columns, direction, tmp_path)
