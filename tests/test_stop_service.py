"""StopService (unit tier) — terminal operator abandonment, facts only.

A fake stands in for the lifecycle store and the locked-transaction seam — only
``record_stop_locked`` and the handle's ``facts`` read are meaningfully implemented; every
other seam raises loudly if called, including the route release and the ``at`` timestamp,
both owned by ``record_stop_locked``'s own locked transaction, never this layer
(``bzh:store-exclusive-write`` — a claim winning the row lock first must never see a
release stamped before it). The terminal-status guard is re-derived from the locked
handle's own ``facts`` read, never a pre-lock snapshot — mirrors ``DeleteService``'s own
fake shape (``tests/test_delete_service.py``)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.node_steps import Executor
from blizzard.hub.domain.chunks.exclusive import IChunkExclusiveWrites, ILockedChunkRead
from blizzard.hub.domain.chunks.lifecycle import IWriteChunkLifecycleRepository
from blizzard.hub.domain.errors import ChunkNotFound
from blizzard.hub.domain.graph import RESERVED_TERMINAL
from blizzard.hub.domain.stop import ChunkNotStoppable, StopService
from blizzard.hub.domain.work import Chunk, ChunkFacts, EscalationFact, QuestionFact, RouteCreatedFact, TransitionFact

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_CHUNK = Chunk(chunk_id="chk_1", graph_id="gr_1", work_refs=[], minted_at=_T0)


@dataclass
class _FakeChunkRepo:
    """Only ``record_stop_locked`` is live; anything else is a bug."""

    stopped: list[tuple[str, str]] = field(default_factory=list)

    def record_stop_locked(self, handle: ILockedChunkRead, chunk_id: str, *, by: str) -> int:
        self.stopped.append((chunk_id, by))
        return len(self.stopped)

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"StopService should not touch {name!r}")


@dataclass
class _FakeLockedChunkRead:
    chunk_facts: ChunkFacts | None

    def facts(self, chunk_id: str) -> ChunkFacts | None:
        return self.chunk_facts

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"StopService should not touch handle.{name!r}")


@dataclass
class _FakeExclusiveWrites:
    chunk_facts: ChunkFacts | None

    @contextmanager
    def locked(self, chunk_ids: Sequence[str]) -> Iterator[ILockedChunkRead]:
        yield cast(ILockedChunkRead, _FakeLockedChunkRead(self.chunk_facts))


def _service(facts: ChunkFacts | None) -> tuple[StopService, _FakeChunkRepo]:
    repo = _FakeChunkRepo()
    exclusive = cast(IChunkExclusiveWrites, _FakeExclusiveWrites(facts))
    service = StopService(lifecycle=cast(IWriteChunkLifecycleRepository, repo), exclusive=exclusive)
    return service, repo


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
    service, repo = _service(facts_factory())  # type: ignore[operator]

    with pytest.raises(ChunkNotStoppable):
        service.stop(_CHUNK, by="operator")

    assert repo.stopped == []


@pytest.mark.parametrize(
    "facts_factory",
    [_not_ready_facts, _running_facts, _waiting_on_human_facts, _needs_human_facts, _paused_facts, _delivering_facts],
    ids=["not_ready", "running", "waiting_on_human", "needs_human", "paused", "delivering"],
)
def test_stop_allows_every_non_terminal_status(facts_factory: object) -> None:
    service, repo = _service(facts_factory())  # type: ignore[operator]

    service.stop(_CHUNK, by="operator")

    assert repo.stopped == [("chk_1", "operator")]


def test_stop_refusal_carries_the_offending_status_on_the_exception() -> None:
    service, _ = _service(_done_facts())

    with pytest.raises(ChunkNotStoppable) as excinfo:
        service.stop(_CHUNK, by="operator")

    assert excinfo.value.status is ChunkStatus.DONE
    assert excinfo.value.chunk_id == "chk_1"
    assert "done" in str(excinfo.value)
    assert "chk_1" in str(excinfo.value)


def test_stop_records_who_stopped_it() -> None:
    service, repo = _service(_not_ready_facts())

    service.stop(_CHUNK, by="paul")

    assert repo.stopped == [("chk_1", "paul")]


def test_stop_raises_chunk_not_found_for_a_chunk_gone_under_the_lock() -> None:
    """A `None` load off the locked handle means gone under this lock — refuse rather
    than substitute a synthetic status, mirroring `DeleteService.delete`."""
    service, repo = _service(None)

    with pytest.raises(ChunkNotFound):
        service.stop(_CHUNK, by="operator")

    assert repo.stopped == []
