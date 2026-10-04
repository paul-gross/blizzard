"""``Chunk.originating_ref`` — the work ref a chunk was minted for (unit tier)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.chunk.model import Chunk, WorkRef

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _chunk(work_refs: list[WorkRef]) -> Chunk:
    return Chunk(chunk_id="ch_1", graph_id="gr_1", work_refs=work_refs, minted_at=_T0)


def test_the_originating_ref_is_the_first_ref_even_after_a_fold_appends_more() -> None:
    minted = WorkRef(source="hub", ref="7")
    folded = WorkRef(source="github", ref="12")

    assert _chunk([minted, folded]).originating_ref() == minted


def test_a_chunk_holding_no_ref_has_no_originating_ref() -> None:
    assert _chunk([]).originating_ref() is None
