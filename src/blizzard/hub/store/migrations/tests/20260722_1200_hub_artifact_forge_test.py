"""Tests proving the ``20260722_1200_hub_artifact_forge`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.migration_support_store_migrations import assert_frozen_table_lacks_a_later_revisions_columns

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "tree,parent_revision,table,columns,direction",
    [
        pytest.param("hub", "20260721_1600_hub_event_log", "artifacts", ("forge",), "added", id="hub.artifacts.forge"),
    ],
)
def test_frozen_table_lacks_a_later_revisions_columns(
    tree: str, parent_revision: str, table: str, columns: tuple[str, ...], direction: str, tmp_path: Path
) -> None:
    assert_frozen_table_lacks_a_later_revisions_columns(tree, parent_revision, table, columns, direction, tmp_path)
