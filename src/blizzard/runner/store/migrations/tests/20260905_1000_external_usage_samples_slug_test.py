"""Tests proving the ``20260905_1000_external_usage_samples_slug`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner import runtime as runner_runtime
from tests.config_inputs import init_host
from tests.migration_support_store_migrations import (
    assert_frozen_table_lacks_a_later_revisions_columns,
)

pytestmark = [pytest.mark.unit]


def test_external_usage_samples_slug_backfills_the_legacy_anthropic_slug(tmp_path: Path) -> None:
    """``external_usage_samples.slug`` backfills a pre-existing row
    to the legacy Anthropic slug, and downgrading past it drops the column again."""
    config = init_host(tmp_path)  # upgrades to head
    runner = runner_runtime.migration_runner(config)

    def _columns() -> set[str]:
        engine = create_engine_from_url(config.db_url)
        try:
            return {c["name"] for c in sa.inspect(engine).get_columns("external_usage_samples")}
        finally:
            engine.dispose()

    def _slugs() -> list[str]:
        engine = create_engine_from_url(config.db_url)
        try:
            with engine.connect() as conn:
                return [row[0] for row in conn.execute(sa.text("select slug from external_usage_samples")).all()]
        finally:
            engine.dispose()

    # The pre-slug shape — this reshape's own parent, frozen by 20260801_1500.
    runner.downgrade("20260801_1500_runner_external_usage_samples")
    assert "slug" not in _columns()
    engine = create_engine_from_url(config.db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text("insert into external_usage_samples (sampled_at, payload) values (:sampled_at, :payload)"),
                {"sampled_at": "2026-08-01 12:00:00", "payload": None},
            )
    finally:
        engine.dispose()

    runner.upgrade("head")
    assert "slug" in _columns()
    assert _slugs() == ["anthropic"]

    runner.downgrade("20260801_1500_runner_external_usage_samples")
    assert "slug" not in _columns()

    runner.upgrade("head")
    assert "slug" in _columns()


@pytest.mark.parametrize(
    "tree,parent_revision,table,columns,direction",
    [
        pytest.param(
            "runner",
            "20260801_1500_runner_external_usage_samples",
            "external_usage_samples",
            ("slug",),
            "added",
            id="runner.external_usage_samples.slug",
        ),
    ],
)
def test_frozen_table_lacks_a_later_revisions_columns(
    tree: str, parent_revision: str, table: str, columns: tuple[str, ...], direction: str, tmp_path: Path
) -> None:
    assert_frozen_table_lacks_a_later_revisions_columns(tree, parent_revision, table, columns, direction, tmp_path)
