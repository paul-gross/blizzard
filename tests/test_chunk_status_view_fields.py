"""``ChunkStatusView``/``ChunkDecisionStatusView`` are narrowed projections of
``ChunkDetail``/``DecisionView`` (blizzard#521, unit tier) — every field they share with
their full-aggregate counterpart must carry the identical type annotation, so a future
edit to one cannot silently drift the other's shape."""

from __future__ import annotations

import pytest

from blizzard.wire.chunk import ChunkDecisionStatusView, ChunkDetail, ChunkStatusView
from blizzard.wire.decision import DecisionView

pytestmark = pytest.mark.unit


def test_chunk_status_view_fields_match_chunk_detail_annotations() -> None:
    # `decision` is deliberately excluded: both carry a field by that name, but
    # `ChunkStatusView.decision` is the lean `ChunkDecisionStatusView` pinned against
    # `DecisionView` separately below, not against `ChunkDetail.decision` itself.
    shared = (set(ChunkStatusView.model_fields) & set(ChunkDetail.model_fields)) - {"decision"}
    assert "status" in shared and "latest_epoch" in shared and "cost" in shared  # sanity: not vacuous
    for name in shared:
        assert ChunkStatusView.model_fields[name].annotation == ChunkDetail.model_fields[name].annotation, name


def test_chunk_decision_status_view_fields_match_decision_view_annotations() -> None:
    shared = set(ChunkDecisionStatusView.model_fields) & set(DecisionView.model_fields)
    assert "decision_id" in shared and "resolved_choice" in shared and "transitioned" in shared  # sanity
    for name in shared:
        assert ChunkDecisionStatusView.model_fields[name].annotation == DecisionView.model_fields[name].annotation, name
