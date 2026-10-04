"""The queue's rules (unit tier) — per-list ranking, reorder membership, and grouping — pinned by
value over loaded chunks, positions, and edges: no repository, no clock."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, DependencyEdge, PauseFact, RouteCreatedFact
from blizzard.hub.domain.kernel.pagination import encode_cursor
from blizzard.hub.domain.operations.queue import (
    ChunkNotGroupable,
    DuplicateReorderIds,
    FoldWouldCloseCycle,
    NotInList,
    QueueList,
    QueueRanking,
    SelfAnchoredMove,
    merge_targets,
    plan_group,
    replacement_order,
    require_anchor_among,
    require_groupable,
    resolve_move,
)

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_PROMOTED = datetime(2026, 2, 1, tzinfo=UTC)


def _chunk(chunk_id: str, *, minted_at: datetime = _T0) -> Chunk:
    return Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=[], minted_at=minted_at)


_A, _B, _C = _chunk("chk_a"), _chunk("chk_b"), _chunk("chk_c")
_STATUSES = {
    "chk_a": ChunkStatus.READY,
    "chk_b": ChunkStatus.READY,
    "chk_c": ChunkStatus.READY,
    "chk_n": ChunkStatus.NOT_READY,
    "chk_r": ChunkStatus.RUNNING,
}


# --- Ranking ------------------------------------------------------------------


def test_effective_position_is_explicit_then_promotion_then_mint() -> None:
    ranking = QueueRanking(positions={"chk_a": 3.0}, promoted_ats={"chk_a": _PROMOTED, "chk_b": _PROMOTED})
    assert ranking.effective_position(_A) == 3.0
    assert ranking.effective_position(_B) == _PROMOTED.timestamp()
    assert ranking.effective_position(_C) == _T0.timestamp()


def test_order_breaks_a_position_tie_by_chunk_id() -> None:
    ranking = QueueRanking(positions={"chk_a": 1.0, "chk_b": 1.0, "chk_c": 0.0}, promoted_ats={})
    assert [c.chunk_id for c in ranking.ordered([_B, _A, _C])] == ["chk_c", "chk_a", "chk_b"]


def test_tail_is_one_past_the_highest_position_or_zero_for_none() -> None:
    ranking = QueueRanking(positions={"chk_a": 4.0, "chk_b": -2.0}, promoted_ats={})
    assert ranking.tail([_A, _B]) == 5.0
    assert ranking.tail([]) == 0.0


def test_slot_after_lands_on_top_after_the_last_and_on_the_midpoint() -> None:
    ranking = QueueRanking(positions={"chk_a": 1.0, "chk_b": 2.0}, promoted_ats={})
    assert ranking.slot_after([_A, _B], None) == 0.0
    assert ranking.slot_after([], None) == 0.0
    assert ranking.slot_after([_A, _B], _B) == 3.0
    assert ranking.slot_after([_A, _B], _A) == 1.5


def test_slot_after_reports_an_exhausted_gap_for_renormalizing() -> None:
    ranking = QueueRanking(positions={"chk_a": 1.0, "chk_b": math.nextafter(1.0, math.inf)}, promoted_ats={})
    assert ranking.slot_after([_A, _B], _A) is None


def test_a_page_carries_absolute_positions_and_a_cursor_only_when_more_remain() -> None:
    ranking = QueueRanking(positions={"chk_a": 1.0, "chk_b": 2.0, "chk_c": 3.0}, promoted_ats={})
    first = ranking.page([_C, _A, _B], cursor=None, limit=2)
    assert [(e.chunk.chunk_id, e.position) for e in first.entries] == [("chk_a", 0), ("chk_b", 1)]
    assert first.next_cursor == encode_cursor(2.0, "chk_b")
    last = ranking.page([_C, _A, _B], cursor=first.next_cursor, limit=2)
    assert [(e.chunk.chunk_id, e.position) for e in last.entries] == [("chk_c", 2)]
    assert last.next_cursor is None


def test_a_page_refuses_a_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="limit must be at least 1"):
        QueueRanking(positions={}, promoted_ats={}).page([], cursor=None, limit=0)


# --- Reorder membership -------------------------------------------------------


def test_a_replacement_puts_the_named_first_then_the_rest_in_current_order() -> None:
    ordered = replacement_order(QueueList.READY, [_A, _B, _C], ["chk_c", "chk_a"], _STATUSES)
    assert [c.chunk_id for c in ordered] == ["chk_c", "chk_a", "chk_b"]


def test_a_replacement_refuses_a_repeated_id_before_an_outsider() -> None:
    with pytest.raises(DuplicateReorderIds, match=r"^chunk_ids must not repeat$"):
        replacement_order(QueueList.READY, [_A], ["chk_n", "chk_n"], _STATUSES)


def test_a_reorder_naming_a_chunk_from_the_other_list_says_where_it_is() -> None:
    with pytest.raises(NotInList) as excinfo:
        replacement_order(QueueList.READY, [_A, _B], ["chk_n"], _STATUSES)
    assert str(excinfo.value) == "chunk chk_n is not in the ready list (it is not_ready)"
    with pytest.raises(NotInList) as excinfo:
        replacement_order(QueueList.NOT_READY, [], ["chk_a"], _STATUSES)
    assert str(excinfo.value) == "chunk chk_a is not in the not_ready list (it is ready)"


def test_a_reorder_naming_a_chunk_in_neither_list_names_only_the_expected_one() -> None:
    with pytest.raises(NotInList) as excinfo:
        replacement_order(QueueList.READY, [_A], ["chk_r"], _STATUSES)
    assert str(excinfo.value) == "chunk chk_r is not in the ready list"
    assert excinfo.value.actual is None


def test_a_move_resolves_both_ids_against_the_list() -> None:
    assert resolve_move(QueueList.READY, [_A, _B], "chk_b", "chk_a", _STATUSES) == (_B, _A)
    assert resolve_move(QueueList.READY, [_A, _B], "chk_b", None, _STATUSES) == (_B, None)


def test_a_move_refuses_a_self_anchor_before_membership() -> None:
    with pytest.raises(SelfAnchoredMove, match=r"^after_chunk_id must not equal chunk_id$"):
        resolve_move(QueueList.READY, [], "chk_n", "chk_n", _STATUSES)


def test_a_move_refuses_an_anchor_outside_the_list() -> None:
    with pytest.raises(NotInList) as excinfo:
        resolve_move(QueueList.READY, [_A, _B], "chk_a", "chk_n", _STATUSES)
    assert excinfo.value.chunk_id == "chk_n"
    assert excinfo.value.actual is QueueList.NOT_READY


def test_a_reposition_admits_a_top_anchor_or_one_among_the_other_members() -> None:
    require_anchor_among(QueueList.READY, [_A, _B], None, _STATUSES)
    require_anchor_among(QueueList.READY, [_A, _B], _B, _STATUSES)


def test_a_reposition_refuses_an_anchor_outside_the_other_members() -> None:
    with pytest.raises(NotInList) as excinfo:
        require_anchor_among(QueueList.READY, [_A], _C, _STATUSES)
    assert excinfo.value.chunk_id == "chk_c"
    assert excinfo.value.expected is QueueList.READY


# --- Grouping -----------------------------------------------------------------


def test_merge_targets_drop_the_survivor_and_repeats_in_first_named_order() -> None:
    assert merge_targets("chk_a", ["chk_b", "chk_a", "chk_c", "chk_b"]) == ["chk_b", "chk_c"]


def test_a_ready_and_a_not_ready_chunk_both_take_part_in_a_group_at_their_own_status() -> None:
    assert require_groupable("chk_a", ChunkFacts(minted=True, promoted=True)) is ChunkStatus.READY
    assert require_groupable("chk_n", ChunkFacts(minted=True)) is ChunkStatus.NOT_READY


@pytest.mark.parametrize(
    "facts",
    [
        ChunkFacts(minted=True, promoted=True, routes_created=[RouteCreatedFact(created_at=_T0)]),
        ChunkFacts(minted=True, pauses=[PauseFact(paused=True, set_at=_T0, set_by="op")]),
        ChunkFacts(minted=True, stopped=True, stopped_at=_T0),
    ],
    ids=["running", "paused", "stopped"],
)
def test_a_group_refuses_a_chunk_outside_the_pre_claim_window(facts: ChunkFacts) -> None:
    with pytest.raises(ChunkNotGroupable):
        require_groupable("chk_x", facts)


def _edge(dependent: str, prerequisite: str) -> DependencyEdge:
    return DependencyEdge(
        dependency_id=f"dep_{dependent}_{prerequisite}",
        dependent_chunk_id=dependent,
        prerequisite_chunk_id=prerequisite,
        declared_at=_T0,
        declared_by="op",
    )


def test_a_fold_that_would_close_a_cycle_is_refused() -> None:
    # c depends on a; a depends on b. Folding b into c makes a depend on c: a cycle.
    standing = [_edge("chk_c", "chk_a"), _edge("chk_a", "chk_b")]
    with pytest.raises(FoldWouldCloseCycle) as excinfo:
        plan_group(standing, "chk_c", ["chk_b"])
    assert excinfo.value.folded_chunk_ids == ["chk_b"]


def test_a_fold_carries_the_folded_chunks_edges_onto_the_survivor() -> None:
    plan = plan_group([_edge("chk_x", "chk_b")], "chk_a", ["chk_b"])
    assert [(m.dependent_chunk_id, m.prerequisite_chunk_id) for m in plan.mint_by_target["chk_b"]] == [
        ("chk_x", "chk_a")
    ]
