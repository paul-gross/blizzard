"""CompleteService (unit tier) — the operator's manual chunk completion (issue #294).

A fake stands in for the lifecycle store — only ``record_completion`` is meaningfully
implemented; every other seam raises loudly if called, mirroring ``StopService``'s own
split. ``facts`` is the caller's own already-loaded value now, so each test builds it
directly rather than handing it to the service through a fake repo."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.node_steps import Executor
from blizzard.hub.domain.chunks.lifecycle import IWriteChunkLifecycleRepository
from blizzard.hub.domain.complete import CompleteService
from blizzard.hub.domain.graph import RESERVED_TERMINAL
from blizzard.hub.domain.work import Chunk, ChunkFacts, RouteCreatedFact, TransitionFact

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_CHUNK = Chunk(chunk_id="chk_1", graph_id="gr_1", work_refs=[], minted_at=_T0)


@dataclass
class _FakeChunkRepo:
    """Only ``record_completion`` is live — see module docstring."""

    completed: list[tuple[str, str, datetime]] = field(default_factory=list)
    _next_id: int = 1

    def record_completion(self, chunk_id: str, *, by: str, at: datetime) -> int:
        self.completed.append((chunk_id, by, at))
        fact_id = self._next_id
        self._next_id += 1
        return fact_id

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"CompleteService should not touch {name!r}")


def _as_lifecycle(repo: _FakeChunkRepo) -> IWriteChunkLifecycleRepository:
    return cast(IWriteChunkLifecycleRepository, repo)


def _not_ready_facts() -> ChunkFacts:
    return ChunkFacts(minted=True)


def _running_facts() -> ChunkFacts:
    return ChunkFacts(minted=True, routes_created=[RouteCreatedFact(created_at=_T0)])


def _stopped_facts() -> ChunkFacts:
    return ChunkFacts(minted=True, stopped=True, stopped_at=_T0)


def _done_via_transition_facts() -> ChunkFacts:
    return ChunkFacts(
        minted=True,
        delivery_landed=True,
        transitions=[
            TransitionFact(to_node_id=RESERVED_TERMINAL, to_node_executor=Executor.HUB, epoch=1, recorded_at=_T0),
        ],
    )


def _done_via_operator_completion_facts() -> ChunkFacts:
    return ChunkFacts(minted=True, operator_completed=True, operator_completed_at=_T0)


@pytest.mark.parametrize(
    "facts_factory",
    [_not_ready_facts, _running_facts, _stopped_facts],
    ids=["not_ready", "running", "stopped"],
)
def test_complete_allows_every_non_done_status(facts_factory: object) -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo()
    service = CompleteService(lifecycle=_as_lifecycle(repo), clock=clock)

    fact_id = service.complete(_CHUNK, facts=facts_factory(), by="operator")  # type: ignore[operator]

    assert fact_id == 1
    assert repo.completed == [("chk_1", "operator", _T0)]


@pytest.mark.parametrize(
    "facts_factory",
    [_done_via_transition_facts, _done_via_operator_completion_facts],
    ids=["done_via_transition", "done_via_operator_completion"],
)
def test_complete_is_a_no_op_on_an_already_done_chunk(facts_factory: object) -> None:
    """Idempotent by no-op — no second fact, never refused."""
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo()
    service = CompleteService(lifecycle=_as_lifecycle(repo), clock=clock)

    fact_id = service.complete(_CHUNK, facts=facts_factory(), by="operator")  # type: ignore[operator]

    assert fact_id is None
    assert repo.completed == []


def test_complete_records_who_completed_it() -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo()
    service = CompleteService(lifecycle=_as_lifecycle(repo), clock=clock)

    service.complete(_CHUNK, facts=_not_ready_facts(), by="paul")

    assert repo.completed == [("chk_1", "paul", _T0)]


def test_complete_uses_the_injected_clock_not_the_wall_clock() -> None:
    later = datetime(2026, 6, 1, tzinfo=UTC)
    clock = FixedClock(instant=later)
    repo = _FakeChunkRepo()
    service = CompleteService(lifecycle=_as_lifecycle(repo), clock=clock)

    service.complete(_CHUNK, facts=_not_ready_facts(), by="operator")

    assert repo.completed == [("chk_1", "operator", later)]
