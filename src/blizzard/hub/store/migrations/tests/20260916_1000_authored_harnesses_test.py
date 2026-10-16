"""Tests proving the ``20260916_1000_authored_harnesses`` revision."""

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
            "20260914_1000_hub_harness_provenance",
            "graph_sessions",
            ("harnesses",),
            "added",
            id="hub.graph_sessions.harnesses",
        ),
        pytest.param(
            "hub",
            "20260914_1000_hub_harness_provenance",
            "chunks",
            ("default_harnesses",),
            "added",
            id="hub.chunks.default_harnesses",
        ),
        pytest.param(
            "hub",
            "20260914_1000_hub_harness_provenance",
            "routines",
            ("default_harnesses",),
            "added",
            id="hub.routines.default_harnesses",
        ),
    ],
)
def test_frozen_table_lacks_a_later_revisions_columns(
    tree: str, parent_revision: str, table: str, columns: tuple[str, ...], direction: str, tmp_path: Path
) -> None:
    assert_frozen_table_lacks_a_later_revisions_columns(tree, parent_revision, table, columns, direction, tmp_path)
