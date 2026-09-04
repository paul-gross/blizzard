"""``derive_blocked_markings`` (unit tier) — the blocked-marking derivation beside a
chunk's status (issue #457).

Pure: no store, no service, standing edges and a status-by-chunk-id mapping in, a
dependent-chunk-id -> prerequisite-chunk-id mapping out."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.dependencies import (
    ChunkNeighbor,
    ChunkNeighborhood,
    derive_blocked_markings,
    derive_chunk_neighborhood,
)
from blizzard.hub.domain.work import DependencyEdge

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _edge(dependency_id: str, dependent: str, prerequisite: str, *, declared_at: datetime = _T0) -> DependencyEdge:
    return DependencyEdge(
        dependency_id=dependency_id,
        dependent_chunk_id=dependent,
        prerequisite_chunk_id=prerequisite,
        declared_at=declared_at,
        declared_by="operator",
    )


def test_unmet_prerequisite_names_a_blocked_marking() -> None:
    edges = [_edge("dep_1", "chk_a", "chk_b")]
    statuses = {"chk_b": ChunkStatus.NOT_READY}

    assert derive_blocked_markings(edges, statuses) == {"chk_a": "chk_b"}


def test_done_prerequisite_clears_the_marking() -> None:
    edges = [_edge("dep_1", "chk_a", "chk_b")]
    statuses = {"chk_b": ChunkStatus.DONE}

    assert derive_blocked_markings(edges, statuses) == {}


def test_released_edge_absent_from_standing_edges_clears_the_marking() -> None:
    """A released edge never reaches this function at all — ``list_standing_edges``
    excludes it — so an empty ``standing_edges`` input is how release clears the marking."""
    assert derive_blocked_markings([], {"chk_b": ChunkStatus.NOT_READY}) == {}


def test_prerequisite_absent_from_statuses_still_blocks() -> None:
    """A standing edge onto an id the status map carries nothing for — a never-resolved or
    since-deleted prerequisite (D3) — is treated exactly as unmet, the conservative read."""
    edges = [_edge("dep_1", "chk_a", "chk_ghost")]

    assert derive_blocked_markings(edges, {}) == {"chk_a": "chk_ghost"}


def test_earliest_declared_unmet_prerequisite_is_named_and_only_it() -> None:
    """Several unmet prerequisites on the same dependent: the earliest-declared wins (D4),
    trusting ``standing_edges``'s own input order rather than re-deriving one."""
    edges = [
        _edge("dep_1", "chk_a", "chk_first", declared_at=_T0),
        _edge("dep_2", "chk_a", "chk_second", declared_at=_T0),
    ]
    statuses = {"chk_first": ChunkStatus.NOT_READY, "chk_second": ChunkStatus.NOT_READY}

    assert derive_blocked_markings(edges, statuses) == {"chk_a": "chk_first"}


def test_a_met_earlier_edge_falls_through_to_a_later_unmet_one() -> None:
    edges = [
        _edge("dep_1", "chk_a", "chk_first", declared_at=_T0),
        _edge("dep_2", "chk_a", "chk_second", declared_at=_T0),
    ]
    statuses = {"chk_first": ChunkStatus.DONE, "chk_second": ChunkStatus.NOT_READY}

    assert derive_blocked_markings(edges, statuses) == {"chk_a": "chk_second"}


def test_a_blocked_prerequisite_is_named_without_walking_its_own_chain() -> None:
    """chk_a depends on chk_b, chk_b itself depends on chk_c: chk_a is marked blocked on
    chk_b only — one hop, no transitive walk to chk_c (D1/D4 scope boundary)."""
    edges = [
        _edge("dep_1", "chk_a", "chk_b"),
        _edge("dep_2", "chk_b", "chk_c"),
    ]
    statuses = {"chk_b": ChunkStatus.NOT_READY, "chk_c": ChunkStatus.NOT_READY}

    assert derive_blocked_markings(edges, statuses) == {"chk_a": "chk_b", "chk_b": "chk_c"}


@pytest.mark.parametrize("dependent_status", [ChunkStatus.READY, ChunkStatus.NOT_READY])
def test_a_pre_claim_dependent_derives_a_marking(dependent_status: ChunkStatus) -> None:
    edges = [_edge("dep_1", "chk_a", "chk_b")]
    statuses = {"chk_a": dependent_status, "chk_b": ChunkStatus.NOT_READY}

    assert derive_blocked_markings(edges, statuses) == {"chk_a": "chk_b"}


@pytest.mark.parametrize(
    "dependent_status",
    [
        ChunkStatus.RUNNING,
        ChunkStatus.DELIVERING,
        ChunkStatus.WAITING_ON_HUMAN,
        ChunkStatus.NEEDS_HUMAN,
        ChunkStatus.PAUSED,
        ChunkStatus.STOPPED,
        ChunkStatus.DONE,
    ],
)
def test_a_dependent_past_the_pre_claim_window_derives_no_marking(dependent_status: ChunkStatus) -> None:
    """Review round 1 F1: the marking answers why a chunk cannot yet be claimed. Once a
    dependent is claimed, running, human-gated, paused, or terminal, that question no
    longer applies even though its edge — declared while it was still pre-claim — persists
    unreleased."""
    edges = [_edge("dep_1", "chk_a", "chk_b")]
    statuses = {"chk_a": dependent_status, "chk_b": ChunkStatus.NOT_READY}

    assert derive_blocked_markings(edges, statuses) == {}


def test_a_dependent_absent_from_statuses_still_derives_a_marking() -> None:
    """A dependent the status map carries nothing for reads the way its default
    ``not_ready`` would — eligible, not excluded — the same conservative-by-default shape
    D3 already gives the prerequisite side."""
    edges = [_edge("dep_1", "chk_a", "chk_b")]

    assert derive_blocked_markings(edges, {"chk_b": ChunkStatus.NOT_READY}) == {"chk_a": "chk_b"}


def test_a_stopped_prerequisite_still_blocks() -> None:
    """Only ``done`` clears an edge (D6/the product plan's "done means done") — a stopped
    prerequisite, itself terminal, still leaves its dependent blocked. Pins the derivation's
    exact predicate against a plausible future widening to ``TERMINAL_STATUSES``, which
    would wrongly also treat ``stopped`` as satisfying."""
    edges = [_edge("dep_1", "chk_a", "chk_b")]
    statuses = {"chk_a": ChunkStatus.READY, "chk_b": ChunkStatus.STOPPED}

    assert derive_blocked_markings(edges, statuses) == {"chk_a": "chk_b"}


class TestDeriveChunkNeighborhood:
    """``derive_chunk_neighborhood`` (unit tier) — the one-hop-each-way sibling of
    ``derive_blocked_markings`` (D3, issue #462): every standing edge naming a chunk in
    either role, with per-edge satisfaction (D4), for a chunk at any status."""

    def test_a_chunk_with_no_edges_has_two_empty_lists(self) -> None:
        assert derive_chunk_neighborhood("chk_a", [], {}) == ChunkNeighborhood(prerequisites=[], dependents=[])

    def test_a_prerequisite_edge_is_satisfied_when_the_prerequisite_is_done(self) -> None:
        edges = [_edge("dep_1", "chk_a", "chk_b")]
        statuses = {"chk_b": ChunkStatus.DONE}

        neighborhood = derive_chunk_neighborhood("chk_a", edges, statuses)

        assert neighborhood == ChunkNeighborhood(
            prerequisites=[ChunkNeighbor(chunk_id="chk_b", status=ChunkStatus.DONE, satisfied=True)], dependents=[]
        )

    def test_a_prerequisite_edge_is_unsatisfied_when_the_prerequisite_is_not_done(self) -> None:
        edges = [_edge("dep_1", "chk_a", "chk_b")]
        statuses = {"chk_b": ChunkStatus.NOT_READY}

        neighborhood = derive_chunk_neighborhood("chk_a", edges, statuses)

        assert neighborhood.prerequisites == [
            ChunkNeighbor(chunk_id="chk_b", status=ChunkStatus.NOT_READY, satisfied=False)
        ]

    def test_a_dependent_edge_is_satisfied_exactly_when_the_subject_itself_is_done(self) -> None:
        """D4: a dependent edge's satisfaction reads the *subject* chunk's own status, not
        the dependent neighbor's — the subject is the prerequisite in that relationship."""
        edges = [_edge("dep_1", "chk_dependent", "chk_a")]
        statuses = {"chk_a": ChunkStatus.DONE, "chk_dependent": ChunkStatus.READY}

        neighborhood = derive_chunk_neighborhood("chk_a", edges, statuses)

        assert neighborhood == ChunkNeighborhood(
            prerequisites=[],
            dependents=[ChunkNeighbor(chunk_id="chk_dependent", status=ChunkStatus.READY, satisfied=True)],
        )

    def test_a_neighbor_absent_from_statuses_is_drawn_unsatisfied_with_a_null_status(self) -> None:
        """A neighbor whose facts do not resolve — the residual race deletion's 409 refusal
        leaves (D4) — is still drawn, never a silently dropped edge."""
        edges = [_edge("dep_1", "chk_a", "chk_ghost")]

        neighborhood = derive_chunk_neighborhood("chk_a", edges, {})

        assert neighborhood.prerequisites == [ChunkNeighbor(chunk_id="chk_ghost", status=None, satisfied=False)]

    def test_both_directions_are_reported_at_once(self) -> None:
        edges = [
            _edge("dep_1", "chk_a", "chk_prereq"),
            _edge("dep_2", "chk_dependent", "chk_a"),
        ]
        statuses = {"chk_a": ChunkStatus.NOT_READY, "chk_prereq": ChunkStatus.DONE, "chk_dependent": ChunkStatus.READY}

        neighborhood = derive_chunk_neighborhood("chk_a", edges, statuses)

        assert neighborhood == ChunkNeighborhood(
            prerequisites=[ChunkNeighbor(chunk_id="chk_prereq", status=ChunkStatus.DONE, satisfied=True)],
            dependents=[ChunkNeighbor(chunk_id="chk_dependent", status=ChunkStatus.READY, satisfied=False)],
        )

    def test_no_transitive_walk_past_one_hop(self) -> None:
        """chk_a's prerequisite is chk_b, chk_b's own prerequisite is chk_c: chk_a's
        neighborhood names chk_b only, unaffected by chk_c (D1/D4 scope boundary)."""
        edges = [
            _edge("dep_1", "chk_a", "chk_b"),
            _edge("dep_2", "chk_b", "chk_c"),
        ]
        statuses = {"chk_b": ChunkStatus.NOT_READY, "chk_c": ChunkStatus.NOT_READY}

        neighborhood = derive_chunk_neighborhood("chk_a", edges, statuses)

        assert neighborhood == ChunkNeighborhood(
            prerequisites=[ChunkNeighbor(chunk_id="chk_b", status=ChunkStatus.NOT_READY, satisfied=False)],
            dependents=[],
        )
