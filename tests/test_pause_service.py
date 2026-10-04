"""PauseService (unit tier) — the operator's per-chunk brake, facts only.

A fake stands in for the lifecycle store — only ``record_pause`` is meaningfully
implemented; every other seam raises loudly if called. ``facts`` is the caller's own
already-loaded value now (``bzh:domain-takes-objects``), so each test builds it directly
rather than handing it to the service through a fake repo."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.node_steps import Executor
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.model import (
    Chunk,
    ChunkFacts,
    EscalationFact,
    QuestionFact,
    RouteCreatedFact,
    TransitionFact,
)
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites, ILockedChunkRead
from blizzard.hub.domain.chunk.ports.lifecycle import IWriteChunkLifecycleRepository
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL
from blizzard.hub.domain.operations.pause import ChunkNotPausable, PauseService

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_CHUNK = Chunk(chunk_id="chk_1", graph_id="gr_1", work_refs=[], minted_at=_T0)


@dataclass
class _FakeChunkRepo:
    """Only the two pause writes and the locked ``facts`` read are live; anything else is a bug.
    ``facts_under_lock`` is what the row-locked read answers."""

    recorded: list[tuple[str, bool, str, datetime]] = field(default_factory=list)
    facts_under_lock: ChunkFacts | None = None

    def record_pause(self, chunk_id: str, *, paused: bool, by: str, at: datetime) -> None:
        self.recorded.append((chunk_id, paused, by, at))

    def record_pause_locked(self, handle: ILockedChunkRead, chunk_id: str, *, by: str, at: datetime) -> None:
        self.recorded.append((chunk_id, True, by, at))

    @contextmanager
    def locked(self, chunk_ids: Sequence[str]) -> Iterator[ILockedChunkRead]:
        yield cast(ILockedChunkRead, self)

    def facts(self, chunk_id: str) -> ChunkFacts | None:
        return self.facts_under_lock

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"PauseService should not touch {name!r}")


def _as_lifecycle(repo: _FakeChunkRepo) -> IWriteChunkLifecycleRepository:
    return cast(IWriteChunkLifecycleRepository, repo)


def _as_exclusive(repo: _FakeChunkRepo) -> IChunkExclusiveWrites:
    return cast(IChunkExclusiveWrites, repo)


def _running_facts() -> ChunkFacts:
    return ChunkFacts(minted=True, routes_created=[RouteCreatedFact(created_at=_T0)])


def _ready_facts() -> ChunkFacts:
    return ChunkFacts(minted=True, promoted=True)


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


@pytest.mark.parametrize(
    "facts_factory",
    [_done_facts, _stopped_facts, _delivering_facts],
    ids=["done", "stopped", "delivering"],
)
def test_pause_refuses_done_stopped_and_delivering(facts_factory: object) -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo()
    service = PauseService(lifecycle=_as_lifecycle(repo), exclusive=_as_exclusive(repo), clock=clock)

    with pytest.raises(ChunkNotPausable):
        repo.facts_under_lock = facts_factory()  # type: ignore[operator]
        service.pause(_CHUNK, by="operator")

    assert repo.recorded == []


@pytest.mark.parametrize(
    "facts_factory",
    [_running_facts, _ready_facts, _waiting_on_human_facts, _needs_human_facts],
    ids=["running", "ready", "waiting_on_human", "needs_human"],
)
def test_pause_allows_running_ready_and_human_gated_statuses(facts_factory: object) -> None:
    # Decided: the lever stays broad — pause is not refused on waiting_on_human/needs_human.
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo()
    service = PauseService(lifecycle=_as_lifecycle(repo), exclusive=_as_exclusive(repo), clock=clock)

    repo.facts_under_lock = facts_factory()  # type: ignore[operator]
    service.pause(_CHUNK, by="operator")

    assert repo.recorded == [("chk_1", True, "operator", _T0)]


@pytest.mark.parametrize(
    "facts_factory",
    [_done_facts, _stopped_facts, _delivering_facts],
    ids=["done", "stopped", "delivering"],
)
def test_resume_is_never_refused_not_even_for_the_statuses_pause_refuses(facts_factory: object) -> None:
    """Resume is unconditional — the refusal set governs `pause` only:
    pause must not engage on finished/in-flight work, but disengaging a brake is
    always safe."""
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo()
    service = PauseService(lifecycle=_as_lifecycle(repo), exclusive=_as_exclusive(repo), clock=clock)

    service.resume(_CHUNK, by="operator")  # no raise, and takes no facts at all

    assert repo.recorded == [("chk_1", False, "operator", _T0)]


def test_resume_twice_is_a_harmless_no_op() -> None:
    """Idempotent by repetition: the second resume is just another newest-wins fact."""
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo()
    service = PauseService(lifecycle=_as_lifecycle(repo), exclusive=_as_exclusive(repo), clock=clock)

    service.resume(_CHUNK, by="operator")
    service.resume(_CHUNK, by="operator")

    assert repo.recorded == [("chk_1", False, "operator", _T0), ("chk_1", False, "operator", _T0)]


def test_pause_refusal_carries_the_offending_status_on_the_exception() -> None:
    """The typed exception carries the status the 409 detail is built from."""
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo()
    service = PauseService(lifecycle=_as_lifecycle(repo), exclusive=_as_exclusive(repo), clock=clock)

    with pytest.raises(ChunkNotPausable) as excinfo:
        repo.facts_under_lock = _delivering_facts()
        service.pause(_CHUNK, by="operator")

    assert excinfo.value.status is ChunkStatus.DELIVERING
    assert excinfo.value.chunk_id == "chk_1"
    assert "delivering" in str(excinfo.value)
    assert "chk_1" in str(excinfo.value)


def test_resume_is_idempotent_on_an_unpaused_chunk() -> None:
    # No refusal at all: resume just appends paused=False, a harmless no-op via
    # newest-fact-wins, matching POST /runners/{id}/resume.
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo()
    service = PauseService(lifecycle=_as_lifecycle(repo), exclusive=_as_exclusive(repo), clock=clock)

    service.resume(_CHUNK, by="operator")

    assert repo.recorded == [("chk_1", False, "operator", _T0)]


def test_set_by_is_carried_onto_the_recorded_fact() -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo()
    service = PauseService(lifecycle=_as_lifecycle(repo), exclusive=_as_exclusive(repo), clock=clock)

    repo.facts_under_lock = _ready_facts()
    service.pause(_CHUNK, by="paul")

    assert repo.recorded == [("chk_1", True, "paul", _T0)]


def test_pause_uses_the_injected_clock_not_the_wall_clock() -> None:
    later = datetime(2026, 6, 1, tzinfo=UTC)
    clock = FixedClock(instant=later)
    repo = _FakeChunkRepo()
    service = PauseService(lifecycle=_as_lifecycle(repo), exclusive=_as_exclusive(repo), clock=clock)

    repo.facts_under_lock = _ready_facts()
    service.pause(_CHUNK, by="operator")

    assert repo.recorded == [("chk_1", True, "operator", later)]


def test_pause_judges_the_status_read_under_the_row_lock_and_refuses_a_chunk_gone_there() -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo()
    service = PauseService(lifecycle=_as_lifecycle(repo), exclusive=_as_exclusive(repo), clock=clock)

    repo.facts_under_lock = None
    with pytest.raises(ChunkNotFound):
        service.pause(_CHUNK, by="operator")

    assert repo.recorded == []
