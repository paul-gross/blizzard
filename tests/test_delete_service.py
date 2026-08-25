"""DeleteService (unit tier) — the operator's deletion of an unacquired chunk,
withdrawing its hub items (issue #364).

A fake stands in for each repository — only the methods :class:`DeleteService` actually
calls are meaningfully implemented; every other seam raises loudly if called. Mirrors
``CompleteService``'s own fake shape (``tests/test_complete_service.py``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.delete import ChunkNotDeletable, DeleteService
from blizzard.hub.domain.graph import RESERVED_TERMINAL, Executor
from blizzard.hub.domain.queue import ChunkNotFound
from blizzard.hub.domain.work import (
    Chunk,
    ChunkFacts,
    EscalationFact,
    IWriteChunkRepository,
    IWriteWorkItemRepository,
    PauseFact,
    QuestionFact,
    RouteCreatedFact,
    TransitionFact,
)

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_CHUNK = Chunk(chunk_id="chk_1", graph_id="gr_1", work_refs=[], minted_at=_T0)


@dataclass
class _FakeChunkRepo:
    """Only ``get``/``load_facts`` are live — see module docstring."""

    chunk: Chunk | None
    facts: ChunkFacts | None

    def get(self, chunk_id: str) -> Chunk | None:
        return self.chunk

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        return self.facts

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"DeleteService should not touch chunks.{name!r}")


@dataclass
class _FakeItemsRepo:
    """Only ``delete_chunk_and_withdraw_hub_items`` is live — see module docstring."""

    deleted: list[tuple[str, str, datetime]] = field(default_factory=list)
    _next_id: int = 1

    def delete_chunk_and_withdraw_hub_items(self, chunk: Chunk, *, by: str, at: datetime) -> int:
        self.deleted.append((chunk.chunk_id, by, at))
        fact_id = self._next_id
        self._next_id += 1
        return fact_id

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"DeleteService should not touch items.{name!r}")


def _service(
    chunk: Chunk | None, facts: ChunkFacts | None, *, clock: FixedClock | None = None
) -> tuple[DeleteService, _FakeItemsRepo]:
    items = _FakeItemsRepo()
    chunks = cast(IWriteChunkRepository, _FakeChunkRepo(chunk=chunk, facts=facts))
    service = DeleteService(
        chunks=chunks, items=cast(IWriteWorkItemRepository, items), clock=clock or FixedClock(instant=_T0)
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
    service, items = _service(_CHUNK, facts_factory())  # type: ignore[operator]

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
    """Deletion is refused at every status outside ``GROUPABLE_STATUSES`` — paused
    included, matching the plan's own explicit callout."""
    service, items = _service(_CHUNK, facts_factory())  # type: ignore[operator]

    with pytest.raises(ChunkNotDeletable):
        service.delete(_CHUNK, by="operator")

    assert items.deleted == []


def test_delete_names_the_chunk_and_status_in_its_refusal_message() -> None:
    service, _ = _service(_CHUNK, _running_facts())

    with pytest.raises(ChunkNotDeletable) as excinfo:
        service.delete(_CHUNK, by="operator")

    assert "chk_1" in str(excinfo.value)
    assert "running" in str(excinfo.value)
    assert "deletion" in str(excinfo.value)  # names deletion, not grouping (D2)


def test_delete_raises_chunk_not_found_for_an_already_gone_chunk() -> None:
    """D5/idempotent-by-guard: a chunk already grouped or deleted away resolves to
    ``None`` from both ``get``/``load_facts`` — the guard raises before any write."""
    service, items = _service(None, None)

    with pytest.raises(ChunkNotFound):
        service.delete(_CHUNK, by="operator")

    assert items.deleted == []


def test_delete_uses_the_injected_clock_not_the_wall_clock() -> None:
    later = datetime(2026, 6, 1, tzinfo=UTC)
    service, items = _service(_CHUNK, _not_ready_facts(), clock=FixedClock(instant=later))

    service.delete(_CHUNK, by="operator")

    assert items.deleted == [("chk_1", "operator", later)]
