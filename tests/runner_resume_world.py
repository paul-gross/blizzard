"""Helpers for the runner restart resume tests, shared with other test modules."""

from __future__ import annotations

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.runner.node_steps.chunk_state import ChunkState


def running_chunk(chunk: str = "ch_1", *, runner_id: str = "r1") -> ChunkState:
    return ChunkState(
        chunk_id=chunk,
        status=ChunkStatus.RUNNING,
        latest_epoch=1,
        route_runner_id=runner_id,
    )
