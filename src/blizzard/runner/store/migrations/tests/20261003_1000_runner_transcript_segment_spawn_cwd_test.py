"""Tests proving the ``20261003_1000_runner_transcript_segment_spawn_cwd`` revision."""

# pyright: standard

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.runner import runtime as runner_runtime
from tests.config_inputs import init_host
from tests.migration_support_store_migrations import (
    _spawn_cwd_round_trip,
)

pytestmark = [pytest.mark.unit]


def test_runner_transcript_segments_spawn_cwd_leaves_a_preexisting_row_null(tmp_path: Path) -> None:
    config = init_host(tmp_path)
    row: dict[str, object] = {
        "segment_id": "sg_1",
        "chunk_id": "ch_1",
        "node_id": "nd_1",
        "epoch": 1,
        "generation": 1,
        "lease_id": "lease_1",
        "session_id": "sess_1",
        "harness_id": "claude_code",
        "shipped_bytes": 0,
        "shipped_turns": 0,
        "normalizer_version": "v1",
        "stamped_at": datetime(2026, 10, 3, tzinfo=UTC),
    }

    values = _spawn_cwd_round_trip(
        config,
        runner_runtime.migration_runner(config),
        "20261002_1300_runner_lease_tokens_token_hash_index",
        row,
        registered=True,
    )

    assert values == [None]
