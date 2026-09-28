"""StopService (unit tier) — terminal operator abandonment, facts only.

A fake stands in for the lifecycle store — only ``record_stop`` is meaningfully
implemented; every other seam raises loudly if called, including the route release and
the ``at`` timestamp, both owned by ``record_stop``'s own locked transaction, never this
layer (``bzh:store-exclusive-write`` — a claim winning the row lock first must never see
a release stamped before it). ``facts`` is the caller's own already-loaded value now, so
each test builds it directly."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.node_steps import Executor
from blizzard.hub.domain.chunks.lifecycle import IWriteChunkLifecycleRepository
from blizzard.hub.domain.graph import RESERVED_TERMINAL
from blizzard.hub.domain.stop import ChunkNotStoppable, StopService
from blizzard.hub.domain.work import (
    Chunk,
    ChunkFacts,
    EscalationFact,
    QuestionFact,
    RouteCreatedFact,
    TransitionFact,
)

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_CHUNK = Chunk(chunk_id="chk_1", graph_id="gr_1", work_refs=[], minted_at=_T0)


@dataclass
class _FakeChunkRepo:
    """Only ``record_stop`` is live; anything else is a bug."""

    stopped: list[tuple[str, str]] = field(default_factory=list)

    def record_stop(self, chunk_id: str, *, by: str) -> None:
        self.stopped.append((chunk_id, by))

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"StopService should not touch {name!r}")


def _as_lifecycle(repo: _FakeChunkRepo) -> IWriteChunkLifecycleRepository:
    return cast(IWriteChunkLifecycleRepository, repo)


def _not_ready_facts() -> ChunkFacts:
    return ChunkFacts(minted=True)


def _running_facts() -> ChunkFacts:
    return ChunkFacts(minted=True, routes_created=[RouteCreatedFact(created_at=_T0)])


def _waiting_on_human_facts() -> ChunkFacts:
    return ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_T0)],
        questions=[QuestionFact(question_id="qn_1", asked_at=_T0, answered=False)],
    )


def _needs_human_facts() -> ChunkFacts:
    return ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_T0)],
        escalations=[EscalationFact(epoch=1, recorded_at=_T0)],
    )


def _paused_facts() -> ChunkFacts:
    from blizzard.hub.domain.work import PauseFact

    return ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_T0)],
        pauses=[PauseFact(paused=True, set_at=_T0, set_by="operator")],
    )


def _delivering_facts() -> ChunkFacts:
    return ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_T0)],
        transitions=[TransitionFact(to_node_id="nd_deliver", to_node_executor=Executor.HUB, epoch=1, recorded_at=_T0)],
    )


def _stopped_facts() -> ChunkFacts:
    return ChunkFacts(minted=True, stopped=True)


def _done_facts() -> ChunkFacts:
    return ChunkFacts(
        minted=True,
        delivery_landed=True,
        transitions=[
            TransitionFact(to_node_id=RESERVED_TERMINAL, to_node_executor=Executor.HUB, epoch=1, recorded_at=_T0),
        ],
    )


@pytest.mark.parametrize("facts_factory", [_done_facts, _stopped_facts], ids=["done", "stopped"])
def test_stop_refuses_done_and_stopped(facts_factory: object) -> None:
    repo = _FakeChunkRepo()
    service = StopService(lifecycle=_as_lifecycle(repo))

    with pytest.raises(ChunkNotStoppable):
        service.stop(_CHUNK, facts=facts_factory(), by="operator")  # type: ignore[operator]

    assert repo.stopped == []


@pytest.mark.parametrize(
    "facts_factory",
    [_not_ready_facts, _running_facts, _waiting_on_human_facts, _needs_human_facts, _paused_facts, _delivering_facts],
    ids=["not_ready", "running", "waiting_on_human", "needs_human", "paused", "delivering"],
)
def test_stop_allows_every_non_terminal_status(facts_factory: object) -> None:
    repo = _FakeChunkRepo()
    service = StopService(lifecycle=_as_lifecycle(repo))

    service.stop(_CHUNK, facts=facts_factory(), by="operator")  # type: ignore[operator]

    assert repo.stopped == [("chk_1", "operator")]


def test_stop_refusal_carries_the_offending_status_on_the_exception() -> None:
    repo = _FakeChunkRepo()
    service = StopService(lifecycle=_as_lifecycle(repo))

    with pytest.raises(ChunkNotStoppable) as excinfo:
        service.stop(_CHUNK, facts=_done_facts(), by="operator")

    assert excinfo.value.status is ChunkStatus.DONE
    assert excinfo.value.chunk_id == "chk_1"
    assert "done" in str(excinfo.value)
    assert "chk_1" in str(excinfo.value)


def test_stop_records_who_stopped_it() -> None:
    repo = _FakeChunkRepo()
    service = StopService(lifecycle=_as_lifecycle(repo))

    service.stop(_CHUNK, facts=_not_ready_facts(), by="paul")

    assert repo.stopped == [("chk_1", "paul")]
