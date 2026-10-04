"""``decide_declare`` (unit tier) — what declaring a dependency decides, pinned by value over loaded
edges and facts: no repository, no clock."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.node_steps import Executor
from blizzard.hub.domain.chunk.dependencies import (
    DependencyWouldCloseCycle,
    DependentNotEditable,
    EdgeToDeclare,
    PrerequisiteIsEphemeral,
    decide_declare,
)
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, DependencyEdge, RouteCreatedFact, TransitionFact
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_DEPENDENT = Chunk(chunk_id="chk_a", graph_id="gr_1", work_refs=[], minted_at=_T0)
_NOT_READY = ChunkFacts(minted=True)
_RUNNING = ChunkFacts(minted=True, promoted=True, routes_created=[RouteCreatedFact(created_at=_T0)])


def _edge(dependent: str, prerequisite: str, dependency_id: str = "dep_1") -> DependencyEdge:
    return DependencyEdge(
        dependency_id=dependency_id,
        dependent_chunk_id=dependent,
        prerequisite_chunk_id=prerequisite,
        declared_at=_T0,
        declared_by="user:alice",
    )


def _decide(
    standing: list[DependencyEdge],
    *,
    facts: ChunkFacts | None = _NOT_READY,
    prerequisite: str = "chk_b",
    ephemeral: bool = False,
) -> DependencyEdge | EdgeToDeclare:
    return decide_declare(
        standing,
        dependent=_DEPENDENT,
        dependent_facts=facts,
        prerequisite_chunk_id=prerequisite,
        prerequisite_ephemeral=ephemeral,
    )


def test_a_fresh_pair_at_a_pre_claim_dependent_is_the_pair_to_mint() -> None:
    assert _decide([]) == EdgeToDeclare(dependent_chunk_id="chk_a", prerequisite_chunk_id="chk_b")


def test_a_ready_dependent_is_still_inside_the_window() -> None:
    assert _decide([], facts=ChunkFacts(minted=True, promoted=True)) == EdgeToDeclare("chk_a", "chk_b")


def test_an_already_standing_pair_is_reported_back_as_that_edge() -> None:
    standing = _edge("chk_a", "chk_b")
    assert _decide([standing]) is standing


def test_idempotence_outranks_every_refusal() -> None:
    """A replay writes nothing, so a gone dependent, a dependent past its window, and an ephemeral
    prerequisite all still read the standing edge back."""
    standing = _edge("chk_a", "chk_b")
    assert _decide([standing], facts=None) is standing
    assert _decide([standing], facts=_RUNNING) is standing
    assert _decide([standing], ephemeral=True) is standing


def test_a_dependent_with_no_facts_is_gone() -> None:
    with pytest.raises(ChunkNotFound) as exc_info:
        _decide([], facts=None)
    assert exc_info.value.chunk_id == "chk_a"


def test_a_dependent_past_pre_claim_is_refused_with_its_status() -> None:
    with pytest.raises(DependentNotEditable) as exc_info:
        _decide([], facts=_RUNNING)
    assert (exc_info.value.chunk_id, exc_info.value.status) == ("chk_a", ChunkStatus.RUNNING)


def test_the_window_outranks_ephemerality() -> None:
    with pytest.raises(DependentNotEditable):
        _decide([], facts=_RUNNING, ephemeral=True)


def test_an_ephemeral_prerequisite_is_refused() -> None:
    with pytest.raises(PrerequisiteIsEphemeral) as exc_info:
        _decide([], ephemeral=True)
    assert exc_info.value.chunk_id == "chk_b"


def test_ephemerality_outranks_the_cycle() -> None:
    with pytest.raises(PrerequisiteIsEphemeral):
        _decide([_edge("chk_b", "chk_a")], ephemeral=True)


def test_an_edge_closing_a_cycle_is_refused() -> None:
    with pytest.raises(DependencyWouldCloseCycle) as exc_info:
        _decide([_edge("chk_b", "chk_c"), _edge("chk_c", "chk_a", "dep_2")])
    assert (exc_info.value.dependent_chunk_id, exc_info.value.prerequisite_chunk_id) == ("chk_a", "chk_b")


def test_a_self_edge_is_the_trivial_cycle() -> None:
    with pytest.raises(DependencyWouldCloseCycle):
        _decide([], prerequisite="chk_a")


def test_a_released_edge_does_not_stand_in_the_way_of_a_fresh_one() -> None:
    """Only standing edges are handed in; a pair whose edge was released mints anew."""
    assert _decide([_edge("chk_b", "chk_c")]) == EdgeToDeclare("chk_a", "chk_b")


def test_a_done_dependent_is_refused_as_past_its_window() -> None:
    done = ChunkFacts(
        minted=True,
        promoted=True,
        transitions=[
            TransitionFact(to_node_id=RESERVED_TERMINAL, to_node_executor=Executor.HUB, epoch=1, recorded_at=_T0)
        ],
    )
    with pytest.raises(DependentNotEditable) as exc_info:
        _decide([], facts=done)
    assert exc_info.value.status is ChunkStatus.DONE
