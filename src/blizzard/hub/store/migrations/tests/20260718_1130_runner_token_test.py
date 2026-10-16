"""Tests proving the ``20260718_1130_runner_token`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.migration_support_store_migrations import assert_frozen_table_lacks_a_later_revisions_columns

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "tree,parent_revision,table,columns,direction",
    [
        pytest.param(
            "hub",
            "20260718_0030_hub_node_poll",
            "runner_registrations",
            ("token_hash",),
            "added",
            id="hub.runner_registrations.token_hash",
        ),
    ],
)
def test_frozen_table_lacks_a_later_revisions_columns(
    tree: str, parent_revision: str, table: str, columns: tuple[str, ...], direction: str, tmp_path: Path
) -> None:
    assert_frozen_table_lacks_a_later_revisions_columns(tree, parent_revision, table, columns, direction, tmp_path)
