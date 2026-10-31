"""Tests proving the ``20260713_1218_walking_skeleton_facts`` revision."""

# pyright: standard

from __future__ import annotations

from pathlib import Path

import pytest

from tests.migration_support_pin_hub_api import (
    _assert_table_shape_is_frozen,
)

pytestmark = [pytest.mark.unit]

_WALKING_SKELETON = "20260713_1218_hub_walking_skeleton"


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        ("chunks", {"chunk_id", "graph_id", "minted_at"}),
        ("route_created", {"route_id", "chunk_id", "runner_id", "workspace_id", "created_at"}),
        ("chunk_pm_pointers", {"id", "chunk_id", "provider", "url"}),
    ],
)
def test_the_walking_skeleton_revisions_table_shape_is_frozen_at_its_own_revision(
    tmp_path: Path, table: str, expected: set[str]
) -> None:
    _assert_table_shape_is_frozen(tmp_path, _WALKING_SKELETON, table, expected)
