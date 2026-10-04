"""DeleteService (unit tier) — the operator's deletion of an unacquired chunk,
withdrawing its hub items.

A fake stands in for each repository — only the methods :class:`DeleteService` actually
calls are meaningfully implemented; every other seam raises loudly if called. Mirrors
``CompleteService``'s own fake shape (``tests/test_complete_service.py``)."""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.node_steps import Executor
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.model import (
    Chunk,
    ChunkFacts,
    DependencyEdge,
    EscalationFact,
    IWriteWorkItemRepository,
    PauseFact,
    QuestionFact,
    RouteCreatedFact,
    TransitionFact,
    WorkRef,
)
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites, ILockedChunkRead
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL
from blizzard.hub.domain.operations.delete import ChunkHasDependents, ChunkNotDeletable, DeleteService

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_CHUNK = Chunk(chunk_id="chk_1", graph_id="gr_1", work_refs=[], minted_at=_T0)


@dataclass
class _FakeLockedChunkRead:
    """Only ``facts``/``record``/``standing_edges`` are live — see module docstring."""

    chunk_facts: ChunkFacts | None
    edges: list[DependencyEdge]
    #: The locked handle's own ``record`` read — ``None`` falls back to the caller's
    #: ``chunk``, exactly matching a real handle's first-mint identity.
    current: Chunk | None = None

    def facts(self, chunk_id: str) -> ChunkFacts | None:
        return self.chunk_facts

    def record(self, chunk_id: str) -> Chunk | None:
        return self.current

    def standing_edges(self) -> list[DependencyEdge]:
        return self.edges

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"DeleteService should not touch handle.{name!r}")


@dataclass
class _FakeItemsRepo:
    """Only ``delete_chunk_and_withdraw_hub_items_locked`` is live — see module docstring."""

    deleted: list[tuple[str, str, datetime]] = field(default_factory=list)
    work_refs_seen: list[list[WorkRef]] = field(default_factory=list)
    _next_id: int = 1

    def delete_chunk_and_withdraw_hub_items_locked(
        self, handle: ILockedChunkRead, chunk: Chunk, *, by: str, at: datetime
    ) -> int:
        self.deleted.append((chunk.chunk_id, by, at))
        self.work_refs_seen.append(chunk.work_refs)
        fact_id = self._next_id
        self._next_id += 1
        return fact_id

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"DeleteService should not touch items.{name!r}")


@dataclass
class _FakeExclusiveWrites:
    """``locked`` yields a :class:`_FakeLockedChunkRead` over the same facts/edges the
    fake previously exposed as direct repositories."""

    chunk_facts: ChunkFacts | None
    edges: list[DependencyEdge]
    current: Chunk | None = None

    @contextmanager
    def locked(self, chunk_ids: Sequence[str]) -> Iterator[ILockedChunkRead]:
        yield cast(ILockedChunkRead, _FakeLockedChunkRead(self.chunk_facts, self.edges, self.current))


def _service(
    facts: ChunkFacts | None,
    *,
    clock: FixedClock | None = None,
    standing_edges: list[DependencyEdge] | None = None,
    current: Chunk | None = None,
) -> tuple[DeleteService, _FakeItemsRepo]:
    items = _FakeItemsRepo()
    exclusive = cast(IChunkExclusiveWrites, _FakeExclusiveWrites(facts, standing_edges or [], current))
    service = DeleteService(
        items=cast(IWriteWorkItemRepository, items),
        clock=clock or FixedClock(instant=_T0),
        exclusive=exclusive,
        cycle_lock=threading.Lock(),
    )
    return service, items


def _not_ready_facts() -> ChunkFacts:
    return ChunkFacts(minted=True)


def _ready_facts() -> ChunkFacts:
    return ChunkFacts(minted=True, promoted=True)


def _running_facts() -> ChunkFacts:
    return ChunkFacts(minted=True, promoted=True, routes_created=[RouteCreatedFact(created_at=_T0)])


def _stopped_facts() -> ChunkFacts:
    return ChunkFacts(minted=True, stopped=True, stopped_at=_T0)


def _paused_facts() -> ChunkFacts:
    return ChunkFacts(minted=True, promoted=True, pauses=[PauseFact(paused=True, set_at=_T0, set_by="operator")])


def _waiting_on_human_facts() -> ChunkFacts:
    return ChunkFacts(
        minted=True, promoted=True, questions=[QuestionFact(question_id="qn_1", asked_at=_T0, answered=False)]
    )


def _needs_human_facts() -> ChunkFacts:
    return ChunkFacts(
        minted=True,
        promoted=True,
        escalations=[EscalationFact(epoch=1, recorded_at=_T0, takeover_command="", wrapped_takeover_command="")],
    )


def _delivering_facts() -> ChunkFacts:
    return ChunkFacts(
        minted=True,
        promoted=True,
        transitions=[TransitionFact(to_node_id="nd_hub", to_node_executor=Executor.HUB, epoch=1, recorded_at=_T0)],
    )


def _done_via_transition_facts() -> ChunkFacts:
    return ChunkFacts(
        minted=True,
        transitions=[
            TransitionFact(to_node_id=RESERVED_TERMINAL, to_node_executor=Executor.HUB, epoch=1, recorded_at=_T0)
        ],
    )


def _done_via_operator_completion_facts() -> ChunkFacts:
    return ChunkFacts(minted=True, operator_completed=True, operator_completed_at=_T0)


@pytest.mark.parametrize("facts_factory", [_not_ready_facts, _ready_facts], ids=["not_ready", "ready"])
def test_delete_succeeds_at_every_groupable_status(facts_factory: object) -> None:
    service, items = _service(facts_factory())  # type: ignore[operator]

    fact_id = service.delete(_CHUNK, by="operator")

    assert fact_id == 1
    assert items.deleted == [("chk_1", "operator", _T0)]


@pytest.mark.parametrize(
    "facts_factory",
    [
        _running_facts,
        _stopped_facts,
        _paused_facts,
        _waiting_on_human_facts,
        _needs_human_facts,
        _delivering_facts,
        _done_via_transition_facts,
        _done_via_operator_completion_facts,
    ],
    ids=[
        "running",
        "stopped",
        "paused",
        "waiting_on_human",
        "needs_human",
        "delivering",
        "done_via_transition",
        "done_via_operator_completion",
    ],
)
def test_delete_refuses_a_non_groupable_status(facts_factory: object) -> None:
    """Deletion is refused at every status outside ``PRE_CLAIM_STATUSES`` — ``paused``
    refused right alongside the runner-held and terminal statuses, not a special case."""
    service, items = _service(facts_factory())  # type: ignore[operator]

    with pytest.raises(ChunkNotDeletable):
        service.delete(_CHUNK, by="operator")

    assert items.deleted == []


def test_delete_names_the_chunk_and_status_in_its_refusal_message() -> None:
    service, _ = _service(_running_facts())

    with pytest.raises(ChunkNotDeletable) as excinfo:
        service.delete(_CHUNK, by="operator")

    assert "chk_1" in str(excinfo.value)
    assert "running" in str(excinfo.value)
    assert "deletion" in str(excinfo.value)  # names deletion, not grouping


def test_delete_raises_chunk_not_found_for_an_already_gone_chunk() -> None:
    """Idempotent-by-guard: a chunk already grouped or deleted away resolves to
    ``None`` from both ``get``/``load_facts`` — the guard raises before any write."""
    service, items = _service(None)

    with pytest.raises(ChunkNotFound):
        service.delete(_CHUNK, by="operator")

    assert items.deleted == []


def test_delete_uses_the_injected_clock_not_the_wall_clock() -> None:
    later = datetime(2026, 6, 1, tzinfo=UTC)
    service, items = _service(_not_ready_facts(), clock=FixedClock(instant=later))

    service.delete(_CHUNK, by="operator")

    assert items.deleted == [("chk_1", "operator", later)]


def _edge(dependent_chunk_id: str, prerequisite_chunk_id: str) -> DependencyEdge:
    return DependencyEdge(
        dependency_id=f"dep_{dependent_chunk_id}_{prerequisite_chunk_id}",
        dependent_chunk_id=dependent_chunk_id,
        prerequisite_chunk_id=prerequisite_chunk_id,
        declared_at=_T0,
        declared_by="operator",
    )


def test_delete_refuses_a_chunk_that_is_a_standing_prerequisite() -> None:
    """A chunk named as another's prerequisite by a standing edge cannot be deleted
    — refused rather than orphaning the dependent's marking."""
    edges = [_edge("chk_dependent", "chk_1")]
    service, items = _service(_not_ready_facts(), standing_edges=edges)

    with pytest.raises(ChunkHasDependents) as excinfo:
        service.delete(_CHUNK, by="operator")

    assert "chk_1" in str(excinfo.value)
    assert "chk_dependent" in str(excinfo.value)
    assert items.deleted == []


def test_delete_names_every_dependent_in_its_refusal() -> None:
    edges = [_edge("chk_dependent_a", "chk_1"), _edge("chk_dependent_b", "chk_1")]
    service, items = _service(_not_ready_facts(), standing_edges=edges)

    with pytest.raises(ChunkHasDependents) as excinfo:
        service.delete(_CHUNK, by="operator")

    assert excinfo.value.dependent_chunk_ids == ["chk_dependent_a", "chk_dependent_b"]
    assert "chk_dependent_a" in str(excinfo.value)
    assert "chk_dependent_b" in str(excinfo.value)
    assert items.deleted == []


def test_delete_succeeds_for_a_chunk_that_is_itself_a_dependent() -> None:
    """``chk_1`` holds its own outgoing edge (it is the dependent, not the
    prerequisite) — deletion is not refused on an outgoing edge; the release of that
    edge is proven at component tier, against the real store."""
    edges = [_edge("chk_1", "chk_prerequisite")]
    service, items = _service(_not_ready_facts(), standing_edges=edges)

    fact_id = service.delete(_CHUNK, by="operator")

    assert fact_id == 1
    assert items.deleted == [("chk_1", "operator", _T0)]


def test_delete_withdraws_the_locked_reads_work_refs_not_the_callers_stale_ones() -> None:
    """A fold that landed a fresh work ref onto this chunk between the caller's own load
    and this delete's lock must have that ref withdrawn too, not silently kept alive
    because the write below still carried the caller's stale, pre-fold ``work_refs``."""
    stale_chunk = Chunk(chunk_id="chk_1", graph_id="gr_1", work_refs=[], minted_at=_T0)
    fresh_ref = WorkRef(source="hub", ref="folded-in")
    fresh_record = Chunk(chunk_id="chk_1", graph_id="gr_1", work_refs=[fresh_ref], minted_at=_T0)
    service, items = _service(_not_ready_facts(), current=fresh_record)

    service.delete(stale_chunk, by="operator")

    assert items.work_refs_seen == [[fresh_ref]]
