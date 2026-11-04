"""``WorkStatusMarker.of`` — a pure, exhaustive derivation over ``ChunkStatus``."""

from __future__ import annotations

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.work_sources.annotator import WorkStatusMarker


def test_marker_of_is_exhaustive_over_chunk_status() -> None:
    """Fails the moment a new `ChunkStatus` member is added and left unmapped."""
    for status in ChunkStatus:
        WorkStatusMarker.of(status)


@pytest.mark.parametrize("status", [ChunkStatus.NOT_READY, ChunkStatus.READY])
def test_marker_of_maps_unclaimed_statuses_to_ingested(status: ChunkStatus) -> None:
    assert WorkStatusMarker.of(status) is WorkStatusMarker.INGESTED


@pytest.mark.parametrize(
    "status",
    [
        ChunkStatus.RUNNING,
        ChunkStatus.PAUSED,
        ChunkStatus.WAITING_ON_HUMAN,
        ChunkStatus.NEEDS_HUMAN,
        ChunkStatus.DELIVERING,
    ],
)
def test_marker_of_maps_live_statuses_to_in_progress(status: ChunkStatus) -> None:
    assert WorkStatusMarker.of(status) is WorkStatusMarker.IN_PROGRESS


@pytest.mark.parametrize("status", [ChunkStatus.STOPPED, ChunkStatus.DONE])
def test_marker_of_maps_terminal_statuses_to_none(status: ChunkStatus) -> None:
    assert WorkStatusMarker.of(status) is None
