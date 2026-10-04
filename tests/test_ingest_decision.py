"""``decide_ingest`` and its parts (unit tier) — what an ingest decides, pinned by value: no
repository, no clock."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.chunk.ingest import (
    EmptyIngest,
    IngestConflict,
    decide_ingest,
    ingest_work_refs,
    require_unheld,
)
from blizzard.hub.domain.chunk.model import WorkRef
from tests.support import make_graph

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_A = WorkRef(source="forge", ref="1")
_B = WorkRef(source="forge", ref="2")
_A_ELSEWHERE = WorkRef(source="tracker", ref="1")
_GRAPH = make_graph("gr_1", "default")


def test_an_empty_batch_is_refused() -> None:
    with pytest.raises(EmptyIngest):
        ingest_work_refs([])


def test_a_ref_named_twice_is_wrapped_once_in_first_seen_order() -> None:
    assert ingest_work_refs([_B, _A, _B, _A]) == [_B, _A]


def test_the_same_ref_under_another_source_is_a_distinct_item() -> None:
    assert ingest_work_refs([_A, _A_ELSEWHERE]) == [_A, _A_ELSEWHERE]


def test_unheld_pointers_pass() -> None:
    require_unheld([_A, _B], {})


def test_the_first_held_pointer_in_submitted_order_is_the_conflict() -> None:
    with pytest.raises(IngestConflict) as exc_info:
        require_unheld([_A, _B], {_B: "chk_holder_b", _A: "chk_holder_a"})
    assert (exc_info.value.pointer, exc_info.value.existing_chunk_id) == (_A, "chk_holder_a")


def test_decide_ingest_mints_one_chunk_over_the_collapsed_refs_at_the_given_instant() -> None:
    chunk = decide_ingest([_A, _B, _A], live_holders={}, graph=_GRAPH, at=_T0)

    assert chunk.work_refs == [_A, _B]
    assert chunk.graph_id == "gr_1"
    assert chunk.minted_at == _T0


def test_decide_ingest_refuses_an_empty_batch_before_any_holder() -> None:
    with pytest.raises(EmptyIngest):
        decide_ingest([], live_holders={_A: "chk_holder"}, graph=_GRAPH, at=_T0)


def test_decide_ingest_refuses_a_batch_naming_a_held_pointer() -> None:
    with pytest.raises(IngestConflict) as exc_info:
        decide_ingest([_A, _B], live_holders={_B: "chk_holder"}, graph=_GRAPH, at=_T0)
    assert exc_info.value.pointer == _B
