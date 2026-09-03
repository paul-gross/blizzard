"""GroupService (unit tier) — folding chunks into a survivor, carrying each folded
chunk's dependency edges onto it (issue #460).

A fake stands in for each repository — only the methods :class:`GroupService` actually
calls are meaningfully implemented; every other seam raises loudly if called. Mirrors
``DeleteService``'s own fake shape (``tests/test_delete_service.py``)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunks.dependencies import FoldTarget, IWriteChunkDependenciesRepository
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunks.record import IReadChunkRecordRepository
from blizzard.hub.domain.chunks.work_refs import IWriteChunkWorkRefsRepository
from blizzard.hub.domain.queue import ChunkNotFound, ChunkNotGroupable, FoldWouldCloseCycle, GroupService
from blizzard.hub.domain.work import Chunk, ChunkFacts, DependencyEdge, RouteCreatedFact, WorkRef

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _chunk(chunk_id: str, *, work_refs: list[WorkRef] | None = None) -> Chunk:
    return Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=work_refs or [], minted_at=_T0)


def _edge(dependent_chunk_id: str, prerequisite_chunk_id: str, *, dependency_id: str | None = None) -> DependencyEdge:
    return DependencyEdge(
        dependency_id=dependency_id or f"dep_{dependent_chunk_id}_{prerequisite_chunk_id}",
        dependent_chunk_id=dependent_chunk_id,
        prerequisite_chunk_id=prerequisite_chunk_id,
        declared_at=_T0,
        declared_by="operator",
    )


def _not_ready_facts() -> ChunkFacts:
    return ChunkFacts(minted=True)


def _running_facts() -> ChunkFacts:
    return ChunkFacts(minted=True, promoted=True, routes_created=[RouteCreatedFact(created_at=_T0)])


@dataclass
class _FakeWorkRefsRepo:
    """Only ``add_work_refs`` is live — see module docstring."""

    added: list[tuple[str, list[WorkRef]]] = field(default_factory=list)

    def add_work_refs(self, chunk_id: str, pointers: list[WorkRef], *, at: datetime) -> None:
        self.added.append((chunk_id, pointers))

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"GroupService should not touch work_refs.{name!r}")


@dataclass
class _FakeDependenciesRepo:
    """Only ``list_standing_edges``/``record_fold`` are live — see module docstring."""

    edges: list[DependencyEdge] = field(default_factory=list)
    folds: list[dict[str, Any]] = field(default_factory=list)
    fold_calls: int = 0
    _next_id: int = 1

    def list_standing_edges(self) -> list[DependencyEdge]:
        return self.edges

    def record_fold(
        self,
        targets: list[FoldTarget],
        *,
        grouped_into: str,
        by: str,
        at: datetime,
    ) -> dict[str, int]:
        self.fold_calls += 1
        grouped_ids: dict[str, int] = {}
        for target in targets:
            self.folds.append(
                {
                    "chunk_id": target.chunk_id,
                    "grouped_into": grouped_into,
                    "release": target.release,
                    "mint": target.mint,
                    "by": by,
                    "at": at,
                }
            )
            grouped_ids[target.chunk_id] = self._next_id
            self._next_id += 1
        return grouped_ids

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"GroupService should not touch dependencies.{name!r}")


@dataclass
class _FakeRecordRepo:
    """Only ``get`` is live — see module docstring."""

    chunks: dict[str, Chunk]

    def get(self, chunk_id: str) -> Chunk | None:
        return self.chunks.get(chunk_id)

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"GroupService should not touch record.{name!r}")


@dataclass
class _FakeFactsRepo:
    """Only ``load_facts`` is live — see module docstring."""

    facts: dict[str, ChunkFacts]

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        return self.facts.get(chunk_id)

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"GroupService should not touch facts.{name!r}")


def _service(
    chunks: dict[str, Chunk],
    facts: dict[str, ChunkFacts],
    *,
    edges: list[DependencyEdge] | None = None,
    clock: FixedClock | None = None,
) -> tuple[GroupService, _FakeWorkRefsRepo, _FakeDependenciesRepo]:
    work_refs = _FakeWorkRefsRepo()
    dependencies = _FakeDependenciesRepo(edges=list(edges or []))
    service = GroupService(
        work_refs=cast(IWriteChunkWorkRefsRepository, work_refs),
        dependencies=cast(IWriteChunkDependenciesRepository, dependencies),
        record=cast(IReadChunkRecordRepository, _FakeRecordRepo(chunks=chunks)),
        facts=cast(IReadChunkFactsRepository, _FakeFactsRepo(facts=facts)),
        clock=clock or FixedClock(instant=_T0),
        claim_lock=threading.Lock(),
    )
    return service, work_refs, dependencies


def test_fold_internal_edge_releases_and_mints_nothing() -> None:
    """The survivor already depends on the folded target — both endpoints collapse to
    the survivor after remap, so the edge is released and nothing is minted."""
    chunks = {"chk_survivor": _chunk("chk_survivor"), "chk_target": _chunk("chk_target")}
    facts = {"chk_survivor": _not_ready_facts(), "chk_target": _not_ready_facts()}
    edges = [_edge("chk_survivor", "chk_target")]
    service, _, dependencies = _service(chunks, facts, edges=edges)

    service.group("chk_survivor", ["chk_target"])

    assert len(dependencies.folds) == 1
    fold = dependencies.folds[0]
    assert fold["chunk_id"] == "chk_target"
    assert fold["release"] == ["dep_chk_survivor_chk_target"]
    assert fold["mint"] == []


def test_edge_duplicating_a_standing_survivor_edge_releases_and_mints_nothing() -> None:
    """The target's edge to an outside chunk duplicates one already standing directly on
    the survivor — released, but no second edge minted."""
    chunks = {"chk_survivor": _chunk("chk_survivor"), "chk_target": _chunk("chk_target")}
    facts = {"chk_survivor": _not_ready_facts(), "chk_target": _not_ready_facts()}
    edges = [
        _edge("chk_survivor", "chk_outside", dependency_id="dep_survivor_outside"),
        _edge("chk_target", "chk_outside", dependency_id="dep_target_outside"),
    ]
    service, _, dependencies = _service(chunks, facts, edges=edges)

    service.group("chk_survivor", ["chk_target"])

    assert len(dependencies.folds) == 1
    fold = dependencies.folds[0]
    assert fold["release"] == ["dep_target_outside"]
    assert fold["mint"] == []


def test_two_targets_sharing_an_outside_edge_mint_only_once_across_the_fold() -> None:
    """Two folded targets each depend on the same outside chunk, same direction — only
    the first target's remapped pair mints; the second's collapses as a duplicate."""
    chunks = {
        "chk_survivor": _chunk("chk_survivor"),
        "chk_a": _chunk("chk_a"),
        "chk_b": _chunk("chk_b"),
    }
    facts = {cid: _not_ready_facts() for cid in chunks}
    edges = [
        _edge("chk_a", "chk_outside", dependency_id="dep_a_outside"),
        _edge("chk_b", "chk_outside", dependency_id="dep_b_outside"),
    ]
    service, _, dependencies = _service(chunks, facts, edges=edges)

    service.group("chk_survivor", ["chk_a", "chk_b"])

    assert len(dependencies.folds) == 2
    by_chunk = {fold["chunk_id"]: fold for fold in dependencies.folds}
    assert by_chunk["chk_a"]["release"] == ["dep_a_outside"]
    assert by_chunk["chk_a"]["mint"] == [("chk_survivor", "chk_outside")]
    assert by_chunk["chk_b"]["release"] == ["dep_b_outside"]
    assert by_chunk["chk_b"]["mint"] == []  # the pair already resulted from chk_a's mint


def test_two_targets_with_an_edge_between_them_reach_the_seam_in_one_fold_call() -> None:
    """``chk_b`` depends on ``chk_a``, both folded into the same survivor (F4, issue
    #460): every target's split reaches the seam in one ``record_fold`` call, so a real
    store can't let one target's row commit ahead of the other's edge release."""
    chunks = {"chk_survivor": _chunk("chk_survivor"), "chk_a": _chunk("chk_a"), "chk_b": _chunk("chk_b")}
    facts = {cid: _not_ready_facts() for cid in chunks}
    edges = [_edge("chk_b", "chk_a", dependency_id="dep_b_a")]
    service, _, dependencies = _service(chunks, facts, edges=edges)

    service.group("chk_survivor", ["chk_a", "chk_b"])

    assert dependencies.fold_calls == 1
    by_chunk = {fold["chunk_id"]: fold for fold in dependencies.folds}
    assert by_chunk["chk_b"]["release"] == ["dep_b_a"]
    assert by_chunk["chk_b"]["mint"] == []
    assert by_chunk["chk_a"]["release"] == []
    assert by_chunk["chk_a"]["mint"] == []


def test_a_genuinely_new_edge_is_released_and_re_minted_attributed_to_its_own_target() -> None:
    """An edge naming no other folded or duplicate pair is released and re-minted with
    the remapped pair, attributed to the target chunk that owned the original edge."""
    chunks = {"chk_survivor": _chunk("chk_survivor"), "chk_target": _chunk("chk_target")}
    facts = {cid: _not_ready_facts() for cid in chunks}
    edges = [_edge("chk_dependent", "chk_target", dependency_id="dep_dependent_target")]
    service, _, dependencies = _service(chunks, facts, edges=edges)

    service.group("chk_survivor", ["chk_target"])

    assert len(dependencies.folds) == 1
    fold = dependencies.folds[0]
    assert fold["chunk_id"] == "chk_target"
    assert fold["release"] == ["dep_dependent_target"]
    assert fold["mint"] == [("chk_dependent", "chk_survivor")]
    assert fold["by"] == "fold"


def test_fold_that_would_close_a_cycle_raises_and_writes_nothing() -> None:
    """The survivor already depends (directly) on ``chk_c``, and ``chk_target`` — being
    folded in — is a standing prerequisite of the survivor's own dependent chain, so
    carrying its edge closes a loop. Refused before any write."""
    chunks = {
        "chk_survivor": _chunk("chk_survivor"),
        "chk_target": _chunk("chk_target"),
        "chk_c": _chunk("chk_c"),
    }
    facts = {cid: _not_ready_facts() for cid in chunks}
    edges = [
        # chk_survivor depends on chk_c, chk_c depends on chk_target: folding chk_target
        # into chk_survivor would carry chk_c -> chk_survivor, closing survivor -> c -> survivor.
        _edge("chk_survivor", "chk_c", dependency_id="dep_survivor_c"),
        _edge("chk_c", "chk_target", dependency_id="dep_c_target"),
    ]
    service, work_refs, dependencies = _service(chunks, facts, edges=edges)

    with pytest.raises(FoldWouldCloseCycle):
        service.group("chk_survivor", ["chk_target"])

    assert dependencies.folds == []
    assert work_refs.added == []


def test_group_still_refuses_an_unacquired_status_violation_and_writes_nothing() -> None:
    chunks = {"chk_survivor": _chunk("chk_survivor"), "chk_running": _chunk("chk_running")}
    facts = {"chk_survivor": _not_ready_facts(), "chk_running": _running_facts()}
    service, work_refs, dependencies = _service(chunks, facts)

    with pytest.raises(ChunkNotGroupable):
        service.group("chk_survivor", ["chk_running"])

    assert dependencies.folds == []
    assert work_refs.added == []


def test_group_still_raises_chunk_not_found_for_an_unknown_target_and_writes_nothing() -> None:
    chunks = {"chk_survivor": _chunk("chk_survivor")}
    facts = {"chk_survivor": _not_ready_facts()}
    service, work_refs, dependencies = _service(chunks, facts)

    with pytest.raises(ChunkNotFound):
        service.group("chk_survivor", ["chk_unknown"])

    assert dependencies.folds == []
    assert work_refs.added == []
