"""StopService (unit tier) — terminal operator abandonment, facts only.

A fake stands in for the lifecycle store and the locked-transaction seam — only
``record_stop`` and the locked ``load_facts`` read are meaningfully implemented; every
other seam raises loudly if called. The route release is owned by ``record_stop``'s
own locked transaction, never this layer. The service stamps ``at`` from its injected clock
once the row lock is held, never before it (``bzh:store-exclusive-write`` — a claim
winning the row lock first must never see a release stamped before it); the fake lock
advances the clock while it waits so the stamp pins that ordering. The terminal-status guard is re-derived from the locked
transaction's own ``load_facts`` read, never a pre-lock snapshot — mirrors ``DeleteService``'s own
fake shape (``tests/unit/hub/domain/operations/test_delete_service.py``)."""

from __future__ import annotations

from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.node_steps import Executor
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.flow_writes import LeverWrite, OpenSubmission
from blizzard.hub.domain.chunk.model import (
    Chunk,
    ChunkFacts,
    EscalationFact,
    QuestionFact,
    RouteCreatedFact,
    TransitionFact,
)
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.lifecycle import IWriteChunkLifecycleRepository
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL
from blizzard.hub.domain.kernel.transactions import IHubTransactions, LockedTransaction, Transaction
from blizzard.hub.domain.operations.stop import ChunkNotStoppable, StopService

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_LOCK_WAIT = timedelta(seconds=5)
_CHUNK = Chunk(chunk_id="chk_1", graph_id="gr_1", work_refs=[], minted_at=_T0)


@dataclass
class _FakeChunkRepo:
    """Only ``record_stop`` is live; anything else is a bug."""

    stopped: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    stamps: list[datetime] = field(default_factory=list[datetime])

    waiting: list[tuple[str, ...]] = field(default_factory=list[tuple[str, ...]])

    def record_stop(
        self, chunk_id: str, *, by: str, at: datetime, waiting: Sequence[str], transaction: LockedTransaction
    ) -> LeverWrite:
        assert transaction is _UNIT, "the stop is written on the unit's locked transaction"
        self.stopped.append((chunk_id, by))
        self.waiting.append(tuple(waiting))
        self.stamps.append(at)
        return LeverWrite(len(self.stopped))

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"StopService should not touch {name!r}")


_UNIT = cast(LockedTransaction, object())


@dataclass
class _FakeFlowSubmissions:
    """The chunk's open submissions — one mid-turn between two waiting — read only in the handle's unit."""

    def open_flow_submissions(
        self, chunk_id: str, *, transaction: Transaction | None = None
    ) -> tuple[OpenSubmission, ...]:
        assert transaction is _UNIT, "the waiting set is read on the handle's transaction"
        return (
            OpenSubmission(submission_id="fs_1", turn_open=False),
            OpenSubmission(submission_id="fs_2", turn_open=True),
            OpenSubmission(submission_id="fs_3", turn_open=False),
        )


class _FakeUnit:
    def lock_chunks(self, chunk_ids: Sequence[str]) -> LockedTransaction:
        return _UNIT


@dataclass
class _FakeFacts:
    chunk_facts: ChunkFacts | None

    def load_facts(self, chunk_id: str, *, transaction: Transaction | None = None) -> ChunkFacts | None:
        assert transaction is _UNIT, "the terminal guard is re-derived on the locked transaction"
        return self.chunk_facts


@dataclass
class _FakeTransactions:
    clock: FixedClock

    @contextmanager
    def begin(self) -> Generator[Transaction]:
        self.clock.advance(_LOCK_WAIT)  # time passes while the lock is awaited
        yield cast(Transaction, _FakeUnit())


def _service(facts: ChunkFacts | None) -> tuple[StopService, _FakeChunkRepo]:
    repo = _FakeChunkRepo()
    clock = FixedClock(_T0)
    service = StopService(
        lifecycle=cast(IWriteChunkLifecycleRepository, repo),
        submissions=_FakeFlowSubmissions(),
        facts=cast(IReadChunkFactsRepository, _FakeFacts(facts)),
        transactions=cast(IHubTransactions, _FakeTransactions(clock)),
        clock=clock,
    )
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
    from blizzard.hub.domain.chunk.model import PauseFact

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
def test_stop_refuses_done_and_stopped(facts_factory: Callable[[], ChunkFacts]) -> None:
    service, repo = _service(facts_factory())

    with pytest.raises(ChunkNotStoppable):
        service.stop(_CHUNK, by="operator")

    assert repo.stopped == []


@pytest.mark.parametrize(
    "facts_factory",
    [_not_ready_facts, _running_facts, _waiting_on_human_facts, _needs_human_facts, _paused_facts, _delivering_facts],
    ids=["not_ready", "running", "waiting_on_human", "needs_human", "paused", "delivering"],
)
def test_stop_allows_every_non_terminal_status(facts_factory: Callable[[], ChunkFacts]) -> None:
    service, repo = _service(facts_factory())

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


def test_stop_stamps_at_after_the_row_lock_is_taken() -> None:
    service, repo = _service(_not_ready_facts())

    service.stop(_CHUNK, by="operator")

    assert repo.stamps == [_T0 + _LOCK_WAIT]


def test_stop_names_the_waiting_submissions_it_read_on_the_handle_unit() -> None:
    """The stop withdraws the submissions waiting their turn, in arrival order; one mid-turn is left to its turn."""
    service, repo = _service(_delivering_facts())

    service.stop(_CHUNK, by="operator")

    assert repo.waiting == [("fs_1", "fs_3")]
