"""``plan_fold`` (unit tier) — the dependency-edge rewrite a fold plans, asserted on the
whole plan: each target's releases and mints and the untouched ``remaining`` set the cycle
check runs against."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.hub.domain.chunk.dependencies import plan_fold
from blizzard.hub.domain.chunk.model import DependencyEdge
from blizzard.hub.domain.chunk.ports.dependencies import FoldMint

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _edge(dependent: str, prerequisite: str, *, declared_at: datetime = _T0) -> DependencyEdge:
    return DependencyEdge(
        dependency_id=f"dep_{dependent}_{prerequisite}",
        dependent_chunk_id=dependent,
        prerequisite_chunk_id=prerequisite,
        declared_at=declared_at,
        declared_by="operator",
    )


def test_remaining_is_exactly_the_edges_naming_no_folded_chunk_and_no_superseded_pair() -> None:
    t1, t3 = _T0 - timedelta(hours=3), _T0 - timedelta(hours=1)
    unrelated = _edge("x", "y")
    folded_as_dependent = _edge("a", "z")
    folded_as_prerequisite = _edge("w", "a")
    earlier_on_target = _edge("d", "a", declared_at=t1)
    later_on_survivor = _edge("d", "s", declared_at=t3)

    plan = plan_fold(
        [unrelated, folded_as_dependent, folded_as_prerequisite, earlier_on_target, later_on_survivor],
        "s",
        ["a"],
    )

    assert plan.remaining == [unrelated]


def test_a_superseded_edge_is_released_by_the_target_owning_the_earlier_edge() -> None:
    t1, t3 = _T0 - timedelta(hours=3), _T0 - timedelta(hours=1)
    plan = plan_fold([_edge("d", "a", declared_at=t1), _edge("d", "s", declared_at=t3)], "s", ["a"])

    assert plan.release_by_target == {"a": ["dep_d_a", "dep_d_s"]}
    assert plan.mint_by_target == {"a": [FoldMint("d", "s", t1)]}
    assert plan.remaining == []


def test_an_earlier_standing_survivor_edge_stays_and_the_folded_edge_only_releases() -> None:
    t1, t3 = _T0 - timedelta(hours=3), _T0 - timedelta(hours=1)
    standing = _edge("d", "s", declared_at=t1)

    plan = plan_fold([standing, _edge("d", "a", declared_at=t3)], "s", ["a"])

    assert plan.release_by_target == {"a": ["dep_d_a"]}
    assert plan.mint_by_target == {"a": []}
    assert plan.remaining == [standing]


def test_an_edge_untouching_the_fold_does_not_stop_the_edges_after_it_planning() -> None:
    unrelated = _edge("x", "y")

    plan = plan_fold([unrelated, _edge("d", "a")], "s", ["a"])

    assert plan.release_by_target == {"a": ["dep_d_a"]}
    assert plan.mint_by_target == {"a": [FoldMint("d", "s", _T0)]}
    assert plan.remaining == [unrelated]


def test_a_self_edge_after_collapse_does_not_stop_the_edges_after_it_planning() -> None:
    plan = plan_fold([_edge("s", "a"), _edge("d", "a")], "s", ["a"])

    assert plan.release_by_target == {"a": ["dep_s_a", "dep_d_a"]}
    assert plan.mint_by_target == {"a": [FoldMint("d", "s", _T0)]}


def test_a_duplicate_pair_after_collapse_does_not_stop_the_edges_after_it_planning() -> None:
    plan = plan_fold([_edge("d", "a"), _edge("d", "b"), _edge("e", "b")], "s", ["a", "b"])

    assert plan.release_by_target == {"a": ["dep_d_a"], "b": ["dep_d_b", "dep_e_b"]}
    assert plan.mint_by_target == {"a": [FoldMint("d", "s", _T0)], "b": [FoldMint("e", "s", _T0)]}
    assert plan.remaining == []


def test_folding_two_targets_onto_one_pair_keeps_the_earliest_declared_edge() -> None:
    t1, t2 = _T0 - timedelta(hours=2), _T0 - timedelta(hours=1)

    plan = plan_fold([_edge("d", "b", declared_at=t1), _edge("d", "a", declared_at=t2)], "s", ["a", "b"])

    assert plan.release_by_target == {"a": ["dep_d_a"], "b": ["dep_d_b"]}
    assert plan.mint_by_target == {"a": [], "b": [FoldMint("d", "s", t1)]}
    assert plan.remaining == []
