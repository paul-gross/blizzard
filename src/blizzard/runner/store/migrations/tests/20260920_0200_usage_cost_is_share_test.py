"""Tests proving the ``20260920_0200_usage_cost_is_share`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.migration_support_store_migrations import assert_frozen_table_lacks_a_later_revisions_columns

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "tree,parent_revision,table,columns,direction",
    [
        pytest.param(
            "runner",
            "20260920_0100_usage_reported_cost",
            "usage_facts",
            ("cost_is_share",),
            "added",
            id="runner.usage_facts.cost_is_share",
        ),
    ],
)
def test_frozen_table_lacks_a_later_revisions_columns(
    tree: str, parent_revision: str, table: str, columns: tuple[str, ...], direction: str, tmp_path: Path
) -> None:
    assert_frozen_table_lacks_a_later_revisions_columns(tree, parent_revision, table, columns, direction, tmp_path)
