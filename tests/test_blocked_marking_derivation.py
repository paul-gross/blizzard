"""``derive_blocked_markings`` (unit tier) — the blocked-marking derivation beside a
chunk's status (issue #457).

Pure: no store, no service, standing edges and a status-by-chunk-id mapping in, a
dependent-chunk-id -> prerequisite-chunk-id mapping out."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.dependencies import derive_blocked_markings
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
