"""Tests proving the ``20260728_1500_lease_session_stamps`` revision."""

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
            "20260727_1000_runner_session_preamble_facts",
            "lease_context",
            ("session_name", "resolved_model", "resolved_effort"),
            "added",
            id="runner.lease_context.session_name",
        ),
    ],
)
def test_frozen_table_lacks_a_later_revisions_columns(
    tree: str, parent_revision: str, table: str, columns: tuple[str, ...], direction: str, tmp_path: Path
) -> None:
    assert_frozen_table_lacks_a_later_revisions_columns(tree, parent_revision, table, columns, direction, tmp_path)
