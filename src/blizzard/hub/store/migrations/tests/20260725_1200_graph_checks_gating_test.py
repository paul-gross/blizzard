"""Tests proving the ``20260725_1200_graph_checks_gating`` revision."""

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
            "20260722_1200_hub_artifact_forge",
            "graph_nodes",
            ("checks_cwd", "checks_timeout"),
            "added",
            id="hub.graph_nodes.checks_cwd",
        ),
        pytest.param(
            "hub",
            "20260722_1200_hub_artifact_forge",
            "graph_choices",
            ("requires_checks",),
            "added",
            id="hub.graph_choices.requires_checks",
        ),
    ],
)
def test_frozen_table_lacks_a_later_revisions_columns(
    tree: str, parent_revision: str, table: str, columns: tuple[str, ...], direction: str, tmp_path: Path
) -> None:
    assert_frozen_table_lacks_a_later_revisions_columns(tree, parent_revision, table, columns, direction, tmp_path)
