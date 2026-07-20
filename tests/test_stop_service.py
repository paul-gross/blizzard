"""StopService (unit tier) — terminal operator abandonment, facts only (issue #118).

A fake stands in for the store — only ``load_facts``/``record_stop`` are meaningfully
implemented; every other seam is unreachable from :meth:`StopService.stop` and raises
loudly if a regression starts calling it (``bzh:domain-core`` — no store, no tokens).
Copies :mod:`tests.test_pause_service`'s and :mod:`tests.test_detach_service`'s
fake-repo pattern exactly, including the ``__getattr__`` guard and the documented
``cast`` at the wide-Protocol call site (``bzh:repository-split``).

The live route's release (and the fleet-wide hub-exec slot's) is no longer something
:class:`StopService` orchestrates itself — must-fix 2 from the #118 pre-push review
folded it into :meth:`~blizzard.hub.store.internal.chunk_store.ChunkStore.record_stop`,
a single store transaction, so the domain layer only ever calls ``record_stop`` and the
``__getattr__`` guard below is exactly what pins that: a regression that reaches for
``route_of``/``record_route_released`` from this layer again fails loudly. The route
release (and the hub-exec slot release) are proven end to end at the component tier
in ``test_chunk_stop.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.graph import RESERVED_TERMINAL, Executor
from blizzard.hub.domain.stop import ChunkNotStoppable, StopService
from blizzard.hub.domain.work import (
    Chunk,
    ChunkFacts,
    ChunkStatus,
    EscalationFact,
    IWriteChunkRepository,
    QuestionFact,
    RouteCreatedFact,
    TransitionFact,
)

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_CHUNK = Chunk(chunk_id="chk_1", graph_id="gr_1", pm_pointers=[], minted_at=_T0)


@dataclass
class _FakeChunkRepo:
    """Only ``load_facts``/``record_stop`` are live; anything else is a bug — including
    ``route_of``/``record_route_released``, which moved into ``record_stop``'s own
    store transaction (must-fix 2, see the module docstring) and so must never be
    touched from this layer again.

    Not typed against :class:`IWriteChunkRepository` directly — pyright cannot verify
    ``__getattr__``-backed structural conformance, so callers wrap an instance in
    :func:`_as_write_repo` instead (see :mod:`tests.test_detach_service`)."""

    facts: ChunkFacts | None
    stopped: list[tuple[str, str, datetime]] = field(default_factory=list)

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        return self.facts

    def record_stop(self, chunk_id: str, *, by: str, at: datetime) -> None:
        self.stopped.append((chunk_id, by, at))

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"StopService should not touch {name!r}")


def _as_write_repo(repo: _FakeChunkRepo) -> IWriteChunkRepository:
    """Assert the fake satisfies the Protocol StopService depends on (see module docstring)."""
    return cast(IWriteChunkRepository, repo)


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
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo(facts=facts_factory())  # type: ignore[operator]
    service = StopService(chunks=_as_write_repo(repo), clock=clock)

    with pytest.raises(ChunkNotStoppable):
        service.stop(_CHUNK, by="operator")

    assert repo.stopped == []


@pytest.mark.parametrize(
    "facts_factory",
    [_not_ready_facts, _running_facts, _waiting_on_human_facts, _needs_human_facts, _paused_facts, _delivering_facts],
    ids=["not_ready", "running", "waiting_on_human", "needs_human", "paused", "delivering"],
)
def test_stop_allows_every_non_terminal_status(facts_factory: object) -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo(facts=facts_factory())  # type: ignore[operator]
    service = StopService(chunks=_as_write_repo(repo), clock=clock)

    service.stop(_CHUNK, by="operator")

    assert repo.stopped == [("chk_1", "operator", _T0)]


def test_stop_refusal_carries_the_offending_status_on_the_exception() -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo(facts=_done_facts())
    service = StopService(chunks=_as_write_repo(repo), clock=clock)

    with pytest.raises(ChunkNotStoppable) as excinfo:
        service.stop(_CHUNK, by="operator")

    assert excinfo.value.status is ChunkStatus.DONE
    assert excinfo.value.chunk_id == "chk_1"
    assert "done" in str(excinfo.value)
    assert "chk_1" in str(excinfo.value)


def test_stop_records_who_stopped_it() -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo(facts=_not_ready_facts())
    service = StopService(chunks=_as_write_repo(repo), clock=clock)

    service.stop(_CHUNK, by="paul")

    assert repo.stopped == [("chk_1", "paul", _T0)]


def test_stop_uses_the_injected_clock_not_the_wall_clock() -> None:
    later = datetime(2026, 6, 1, tzinfo=UTC)
    clock = FixedClock(instant=later)
    repo = _FakeChunkRepo(facts=_not_ready_facts())
    service = StopService(chunks=_as_write_repo(repo), clock=clock)

    service.stop(_CHUNK, by="operator")

    assert repo.stopped == [("chk_1", "operator", later)]
