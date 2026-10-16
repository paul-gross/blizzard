"""Tests proving the ``20261003_1300_hub_transcript_segment_spawn_cwd`` revision."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.hub import runtime as hub_runtime
from tests.migration_support_store_migrations import (
    _spawn_cwd_round_trip,
)

pytestmark = [pytest.mark.unit]


def test_hub_transcript_segments_spawn_cwd_leaves_a_preexisting_row_null(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path)
    row: dict[str, object] = {
        "segment_id": "sg_1",
        "chunk_id": "ch_1",
        "node_id": "nd_1",
        "epoch": 1,
        "spawn_generation": 1,
        "runner_id": "r1",
        "turn_range_start": 0,
        "turn_range_end": 0,
        "final": False,
        "rejected": False,
        "byte_count": 0,
        "normalizer_version": "v1",
        "received_at": datetime(2026, 10, 3, tzinfo=UTC),
        "content_digest": "d",
    }

    values = _spawn_cwd_round_trip(config, hub_runtime.migration_runner(config), "20261003_1200_event_drops", row)

    assert values == [None]
