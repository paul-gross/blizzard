"""DependencyService (unit tier) — declaring and releasing a chunk dependency edge, under
the shared claim lock (issue #456).

A fake stands in for the dependencies store and the facts read seam — every unimplemented
method raises loudly if called (``bzh:domain-core``). The lock's cross-declaration race
atomicity is proven at the component tier (``tests/test_dependency_race.py``), not here."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunks.dependencies import IWriteChunkDependenciesRepository
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunks.lifecycle import IReadChunkLifecycleRepository
from blizzard.hub.domain.dependencies import (
    DependencyService,
    DependencyWouldCloseCycle,
    DependentNotEditable,
    NoStandingDependencyToRelease,
    PrerequisiteIsEphemeral,
)
from blizzard.hub.domain.work import Chunk, ChunkFacts, DependencyEdge, RouteCreatedFact

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _chunk(chunk_id: str) -> Chunk:
    return Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=[], minted_at=_T0)


_DEPENDENT = _chunk("chk_dependent")
_PREREQUISITE = _chunk("chk_prereq")


@dataclass
class _FakeFactsRepo:
    """Only ``load_facts`` is live; anything else is a bug (see module docstring).
    ``calls`` records every chunk id asked for, so a test can assert the prerequisite's
    facts are never consulted."""

    facts: ChunkFacts | None
    calls: list[str] = field(default_factory=list)

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        self.calls.append(chunk_id)
        return self.facts

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"DependencyService should not touch {name!r}")


def _as_facts(repo: _FakeFactsRepo) -> IReadChunkFactsRepository:
    return cast(IReadChunkFactsRepository, repo)


@dataclass
class _FakeLifecycleRepo:
    """Only ``is_ephemeral`` is live; anything else is a bug (see module docstring).
    ``ephemeral`` names the chunk ids ``is_ephemeral`` answers ``True`` for; ``calls``
    records every chunk id asked for, so a test can assert the dependent's ephemerality
    is never consulted."""

    ephemeral: frozenset[str] = frozenset()
    calls: list[str] = field(default_factory=list)

    def is_ephemeral(self, chunk_id: str) -> bool:
        self.calls.append(chunk_id)
        return chunk_id in self.ephemeral

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"DependencyService should not touch {name!r}")


def _as_lifecycle(repo: _FakeLifecycleRepo) -> IReadChunkLifecycleRepository:
    return cast(IReadChunkLifecycleRepository, repo)


@dataclass
class _FakeDependenciesRepo:
    """Only the four ``IWriteChunkDependenciesRepository`` methods are live; anything else
    is a bug (see module docstring). ``standing`` seeds the edges already stood before the
    call under test; ``declared``/``released`` record every write this seam was asked to
    make."""

    standing: list[DependencyEdge] = field(default_factory=list)
    declared: list[tuple[str, str, str, datetime]] = field(default_factory=list)
    released: list[tuple[str, str, str, datetime]] = field(default_factory=list)
    _next_id: int = 0

    def list_standing_edges(self) -> list[DependencyEdge]:
        return list(self.standing)

    def standing_edge(self, dependent_chunk_id: str, prerequisite_chunk_id: str) -> DependencyEdge | None:
        for edge in self.standing:
            if edge.dependent_chunk_id == dependent_chunk_id and edge.prerequisite_chunk_id == prerequisite_chunk_id:
                return edge
        return None

    def declare(self, dependent_chunk_id: str, prerequisite_chunk_id: str, *, by: str, at: datetime) -> DependencyEdge:
        self.declared.append((dependent_chunk_id, prerequisite_chunk_id, by, at))
        self._next_id += 1
        edge = DependencyEdge(
            dependency_id=f"dep_{self._next_id}",
            dependent_chunk_id=dependent_chunk_id,
            prerequisite_chunk_id=prerequisite_chunk_id,
            declared_at=at,
            declared_by=by,
        )
        self.standing.append(edge)
        return edge

    def release(
        self, dependent_chunk_id: str, prerequisite_chunk_id: str, *, by: str, at: datetime
    ) -> DependencyEdge | None:
        self.released.append((dependent_chunk_id, prerequisite_chunk_id, by, at))
        for i, edge in enumerate(self.standing):
            if edge.dependent_chunk_id == dependent_chunk_id and edge.prerequisite_chunk_id == prerequisite_chunk_id:
                released_edge = DependencyEdge(
                    dependency_id=edge.dependency_id,
                    dependent_chunk_id=edge.dependent_chunk_id,
                    prerequisite_chunk_id=edge.prerequisite_chunk_id,
                    declared_at=edge.declared_at,
                    declared_by=edge.declared_by,
                    released_at=at,
                    released_by=by,
                )
                self.standing.pop(i)
                return released_edge
        return None

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"DependencyService should not touch {name!r}")


def _as_dependencies(repo: _FakeDependenciesRepo) -> IWriteChunkDependenciesRepository:
    return cast(IWriteChunkDependenciesRepository, repo)


def _not_ready_facts() -> ChunkFacts:
    return ChunkFacts(minted=True)


def _running_facts() -> ChunkFacts:
    return ChunkFacts(minted=True, promoted=True, routes_created=[RouteCreatedFact(created_at=_T0)])


def _service(
    dependencies: _FakeDependenciesRepo,
    facts: _FakeFactsRepo | None = None,
    lifecycle: _FakeLifecycleRepo | None = None,
) -> tuple[DependencyService, _FakeFactsRepo]:
    """Build a ``DependencyService`` over ``dependencies`` with a fresh, single-test claim
    lock (the shared-lock race is proven at the component tier). ``facts`` defaults to a
    not-ready dependent; ``lifecycle`` defaults to no chunk being ephemeral."""
    facts_repo = facts or _FakeFactsRepo(facts=_not_ready_facts())
    lifecycle_repo = lifecycle or _FakeLifecycleRepo()
    return (
        DependencyService(
            facts=_as_facts(facts_repo),
            lifecycle=_as_lifecycle(lifecycle_repo),
            dependencies=_as_dependencies(dependencies),
            clock=FixedClock(instant=_T0),
            claim_lock=threading.Lock(),
        ),
        facts_repo,
    )


def test_declare_on_an_unclaimed_dependent_stores_the_edge() -> None:
    repo = _FakeDependenciesRepo()
    service, _ = _service(repo)

    edge = service.declare(_DEPENDENT, _PREREQUISITE, by="user:alice")

    assert edge.dependent_chunk_id == "chk_dependent"
    assert edge.prerequisite_chunk_id == "chk_prereq"
    assert edge.standing is True
    assert repo.declared == [("chk_dependent", "chk_prereq", "user:alice", _T0)]
    assert repo.standing_edge("chk_dependent", "chk_prereq") == edge


def test_declare_is_an_idempotent_no_op_on_an_already_standing_edge() -> None:
    repo = _FakeDependenciesRepo()
    service, _ = _service(repo)
    first = service.declare(_DEPENDENT, _PREREQUISITE, by="user:alice")

    second = service.declare(_DEPENDENT, _PREREQUISITE, by="user:bob")

    assert second == first
    # No second write — the standing edge is reported, not re-declared.
    assert repo.declared == [("chk_dependent", "chk_prereq", "user:alice", _T0)]


def test_release_on_a_standing_edge_marks_it_released_once() -> None:
    repo = _FakeDependenciesRepo()
    service, _ = _service(repo)
    edge = service.declare(_DEPENDENT, _PREREQUISITE, by="user:alice")

    released = service.release(edge, by="user:bob")

    assert released.standing is False
    assert released.released_at == _T0
    assert released.released_by == "user:bob"
    assert repo.released == [("chk_dependent", "chk_prereq", "user:bob", _T0)]
    assert repo.standing_edge("chk_dependent", "chk_prereq") is None


def test_declare_is_refused_when_the_dependent_has_left_pre_claim_and_writes_nothing() -> None:
    repo = _FakeDependenciesRepo()
    service, _ = _service(repo, _FakeFactsRepo(facts=_running_facts()))

    with pytest.raises(DependentNotEditable) as exc_info:
        service.declare(_DEPENDENT, _PREREQUISITE, by="user:alice")

    assert exc_info.value.chunk_id == "chk_dependent"
    assert exc_info.value.status.value == "running"
    assert repo.declared == []
    assert repo.standing == []


def test_declare_is_refused_when_it_would_close_a_cycle_and_writes_nothing() -> None:
    # Standing: A depends on B, B depends on C. Declaring "C depends on A" closes the cycle.
    repo = _FakeDependenciesRepo()
    service, _ = _service(repo)
    service.declare(_chunk("chk_a"), _chunk("chk_b"), by="user:alice")
    service.declare(_chunk("chk_b"), _chunk("chk_c"), by="user:alice")
    before = list(repo.standing)

    with pytest.raises(DependencyWouldCloseCycle) as exc_info:
        service.declare(_chunk("chk_c"), _chunk("chk_a"), by="user:alice")

    assert exc_info.value.dependent_chunk_id == "chk_c"
    assert exc_info.value.prerequisite_chunk_id == "chk_a"
    assert repo.standing == before


def test_declare_of_a_self_edge_is_refused_as_the_trivial_cycle_and_writes_nothing() -> None:
    repo = _FakeDependenciesRepo()
    service, _ = _service(repo)

    with pytest.raises(DependencyWouldCloseCycle) as exc_info:
        service.declare(_DEPENDENT, _DEPENDENT, by="user:alice")

    assert exc_info.value.dependent_chunk_id == "chk_dependent"
    assert exc_info.value.prerequisite_chunk_id == "chk_dependent"
    assert repo.declared == []
    assert repo.standing == []


def test_release_is_refused_when_the_loaded_edge_no_longer_stands_and_writes_nothing() -> None:
    """The caller loads the edge, then the lock is taken — a release landing in between
    leaves a stale edge object, and the store's own read-then-write refuses it."""
    repo = _FakeDependenciesRepo()
    service, _ = _service(repo)
    stale = DependencyEdge(
        dependency_id="dep_stale",
        dependent_chunk_id="chk_dependent",
        prerequisite_chunk_id="chk_prereq",
        declared_at=_T0,
        declared_by="user:alice",
    )

    with pytest.raises(NoStandingDependencyToRelease) as exc_info:
        service.release(stale, by="user:bob")

    assert exc_info.value.dependent_chunk_id == "chk_dependent"
    assert exc_info.value.prerequisite_chunk_id == "chk_prereq"
    assert repo.standing == []


def test_declare_onto_a_done_prerequisite_is_accepted_with_no_satisfaction_state_written() -> None:
    """Satisfaction is never stored: the service consults nothing about the
    prerequisite beyond its id — not even its facts — so a ``done`` prerequisite is an
    ordinary accepted edge, no special case."""
    repo = _FakeDependenciesRepo()
    service, facts_repo = _service(repo)

    edge = service.declare(_DEPENDENT, _chunk("chk_prereq_done"), by="user:alice")

    assert edge.dependent_chunk_id == "chk_dependent"
    assert edge.prerequisite_chunk_id == "chk_prereq_done"
    # Only the dependent's facts were ever loaded — the prerequisite's status (done or
    # otherwise) is never consulted.
    assert facts_repo.calls == ["chk_dependent"]


def test_re_declaring_a_standing_edge_is_still_a_no_op_after_the_dependent_leaves_pre_claim() -> None:
    """The standing-edge check runs before ``PRE_CLAIM_STATUSES``, so re-declaring an
    edge that already stands is never refused — even once the dependent has left the
    window, since the window gates declaring, not an already-standing relation."""
    standing = DependencyEdge(
        dependency_id="dep_1",
        dependent_chunk_id="chk_dependent",
        prerequisite_chunk_id="chk_prereq",
        declared_at=_T0,
        declared_by="user:alice",
    )
    repo = _FakeDependenciesRepo(standing=[standing])
    service, _ = _service(repo, _FakeFactsRepo(facts=_running_facts()))

    reported = service.declare(_DEPENDENT, _PREREQUISITE, by="user:bob")

    assert reported == standing
    assert repo.declared == []


def test_declare_is_refused_when_the_prerequisite_is_ephemeral_under_the_lock_and_writes_nothing() -> None:
    """The service re-derives the prerequisite's ephemerality itself, under the same lock
    as the dependent's status — a resolved-but-since-ephemeral prerequisite (a race the
    controller's own early-out cannot always catch) is still refused here."""
    repo = _FakeDependenciesRepo()
    lifecycle = _FakeLifecycleRepo(ephemeral=frozenset({"chk_prereq"}))
    service, _ = _service(repo, lifecycle=lifecycle)

    with pytest.raises(PrerequisiteIsEphemeral) as exc_info:
        service.declare(_DEPENDENT, _PREREQUISITE, by="user:alice")

    assert exc_info.value.chunk_id == "chk_prereq"
    assert repo.declared == []
    assert repo.standing == []
    # Only the prerequisite's ephemerality was consulted — the dependent's is never asked.
    assert lifecycle.calls == ["chk_prereq"]
