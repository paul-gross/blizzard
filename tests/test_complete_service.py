"""CompleteService (unit tier) — the operator's manual chunk completion.

A fake stands in for the lifecycle store and the locked-transaction seam — only
``record_completion_locked`` and the handle's ``facts`` read are meaningfully implemented;
every other seam raises loudly if called, mirroring ``StopService``'s own split. The
service stamps ``at`` from its injected clock once the row lock is held, never before it
(``bzh:store-exclusive-write``); the fake lock advances the clock while it waits so the
stamp pins that ordering. The already-``done`` guard is
re-derived from the locked handle's own ``facts`` read, never a pre-lock snapshot."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.node_steps import Executor
from blizzard.hub.domain.chunks.exclusive import IChunkExclusiveWrites, ILockedChunkRead
from blizzard.hub.domain.chunks.lifecycle import IWriteChunkLifecycleRepository
from blizzard.hub.domain.complete import CompleteService
from blizzard.hub.domain.errors import ChunkNotFound
from blizzard.hub.domain.graph import RESERVED_TERMINAL
from blizzard.hub.domain.work import Chunk, ChunkFacts, RouteCreatedFact, TransitionFact

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_LOCK_WAIT = timedelta(seconds=5)
_CHUNK = Chunk(chunk_id="chk_1", graph_id="gr_1", work_refs=[], minted_at=_T0)


@dataclass
class _FakeChunkRepo:
    """Only ``record_completion_locked`` is live — see module docstring."""

    completed: list[tuple[str, str]] = field(default_factory=list)
    stamps: list[datetime] = field(default_factory=list)
    _next_id: int = 1

    def record_completion_locked(self, handle: ILockedChunkRead, chunk_id: str, *, by: str, at: datetime) -> int:
        self.completed.append((chunk_id, by))
        self.stamps.append(at)
        fact_id = self._next_id
        self._next_id += 1
        return fact_id

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"CompleteService should not touch {name!r}")


@dataclass
class _FakeLockedChunkRead:
    chunk_facts: ChunkFacts | None

    def facts(self, chunk_id: str) -> ChunkFacts | None:
        return self.chunk_facts

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"CompleteService should not touch handle.{name!r}")


@dataclass
class _FakeExclusiveWrites:
    chunk_facts: ChunkFacts | None
    clock: FixedClock

    @contextmanager
    def locked(self, chunk_ids: Sequence[str]) -> Iterator[ILockedChunkRead]:
        self.clock.advance(_LOCK_WAIT)  # time passes while the lock is awaited
        yield cast(ILockedChunkRead, _FakeLockedChunkRead(self.chunk_facts))


def _service(facts: ChunkFacts | None) -> tuple[CompleteService, _FakeChunkRepo]:
    repo = _FakeChunkRepo()
    clock = FixedClock(_T0)
    exclusive = cast(IChunkExclusiveWrites, _FakeExclusiveWrites(facts, clock))
    service = CompleteService(lifecycle=cast(IWriteChunkLifecycleRepository, repo), exclusive=exclusive, clock=clock)
    return service, repo


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
    service, repo = _service(facts_factory())  # type: ignore[operator]

    fact_id = service.complete(_CHUNK, by="operator")

    assert fact_id == 1
    assert repo.completed == [("chk_1", "operator")]


@pytest.mark.parametrize(
    "facts_factory",
    [_done_via_transition_facts, _done_via_operator_completion_facts],
    ids=["done_via_transition", "done_via_operator_completion"],
)
def test_complete_is_a_no_op_on_an_already_done_chunk(facts_factory: object) -> None:
    """Idempotent by no-op — no second fact, never refused."""
    service, repo = _service(facts_factory())  # type: ignore[operator]

    fact_id = service.complete(_CHUNK, by="operator")

    assert fact_id is None
    assert repo.completed == []


def test_complete_records_who_completed_it() -> None:
    service, repo = _service(_not_ready_facts())

    service.complete(_CHUNK, by="paul")

    assert repo.completed == [("chk_1", "paul")]


def test_complete_raises_chunk_not_found_for_a_chunk_gone_under_the_lock() -> None:
    """A `None` load off the locked handle means gone under this lock — refuse rather
    than substitute a synthetic status, mirroring `DeleteService.delete`."""
    service, repo = _service(None)

    with pytest.raises(ChunkNotFound):
        service.complete(_CHUNK, by="operator")

    assert repo.completed == []


def test_complete_stamps_at_after_the_row_lock_is_taken() -> None:
    service, repo = _service(_not_ready_facts())

    service.complete(_CHUNK, by="operator")

    assert repo.stamps == [_T0 + _LOCK_WAIT]
