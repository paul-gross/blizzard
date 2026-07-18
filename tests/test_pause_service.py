"""PauseService (unit tier) — the operator's per-chunk brake, facts only (issue #46).

A fake stands in for the store — only ``load_facts``/``record_pause`` are meaningfully
implemented; every other seam is unreachable from :meth:`PauseService.pause`/``resume``
and raises loudly if a regression starts calling it (``bzh:domain-core`` — no store, no
tokens). Copies :mod:`tests.test_detach_service`'s fake-repo pattern exactly, including
its ``__getattr__`` guard and the documented ``cast`` at the wide-Protocol call site
(``bzh:repository-split``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.graph import RESERVED_TERMINAL, Executor
from blizzard.hub.domain.pause import ChunkNotPausable, PauseService
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
    """Only ``load_facts``/``record_pause`` are live; anything else is a bug.

    Not typed against :class:`IWriteChunkRepository` directly — pyright cannot verify
    ``__getattr__``-backed structural conformance, so callers wrap an instance in
    :func:`_as_write_repo` instead (see :mod:`tests.test_detach_service`)."""

    facts: ChunkFacts | None
    recorded: list[tuple[str, bool, str, datetime]] = field(default_factory=list)

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        return self.facts

    def record_pause(self, chunk_id: str, *, paused: bool, by: str, at: datetime) -> None:
        self.recorded.append((chunk_id, paused, by, at))

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"PauseService should not touch {name!r}")


def _as_write_repo(repo: _FakeChunkRepo) -> IWriteChunkRepository:
    """Assert the fake satisfies the Protocol PauseService depends on (see module docstring)."""
    return cast(IWriteChunkRepository, repo)


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
    repo = _FakeChunkRepo(facts=facts_factory())  # type: ignore[operator]
    service = PauseService(chunks=_as_write_repo(repo), clock=clock)

    with pytest.raises(ChunkNotPausable):
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
    repo = _FakeChunkRepo(facts=facts_factory())  # type: ignore[operator]
    service = PauseService(chunks=_as_write_repo(repo), clock=clock)

    service.pause(_CHUNK, by="operator")

    assert repo.recorded == [("chk_1", True, "operator", _T0)]


@pytest.mark.parametrize(
    "facts_factory",
    [_done_facts, _stopped_facts, _delivering_facts],
    ids=["done", "stopped", "delivering"],
)
def test_resume_is_never_refused_not_even_for_the_statuses_pause_refuses(facts_factory: object) -> None:
    """Resume is **unconditional** — the refusal set governs `pause` only (issue #46 §4).

    The asymmetry is a decision, not an oversight: pause is a lever that must not be
    *engaged* on work already finished or in flight to the forge, but *disengaging* a brake
    is always safe, so it is never refused (matching `POST /runners/{id}/resume`). Without
    these, `resume` could grow a `_require_pausable` call and the whole suite stays green —
    and a chunk paused before it reached `done` could then never have its pause fact
    cleared. The CLI relies on this too: `resume-chunk` maps no 409 at all, so a refusal
    would surface as a raw API error rather than a named one.
    """
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo(facts=facts_factory())  # type: ignore[operator]
    service = PauseService(chunks=_as_write_repo(repo), clock=clock)

    service.resume(_CHUNK, by="operator")  # no raise

    assert repo.recorded == [("chk_1", False, "operator", _T0)]


def test_resume_twice_is_a_harmless_no_op() -> None:
    """Idempotent by repetition: the second resume is just another newest-wins fact."""
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo(facts=_ready_facts())
    service = PauseService(chunks=_as_write_repo(repo), clock=clock)

    service.resume(_CHUNK, by="operator")
    service.resume(_CHUNK, by="operator")

    assert repo.recorded == [("chk_1", False, "operator", _T0), ("chk_1", False, "operator", _T0)]


def test_pause_refusal_carries_the_offending_status_on_the_exception() -> None:
    """The typed exception carries the status — the 409 detail the CLI echoes is built from it."""
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo(facts=_delivering_facts())
    service = PauseService(chunks=_as_write_repo(repo), clock=clock)

    with pytest.raises(ChunkNotPausable) as excinfo:
        service.pause(_CHUNK, by="operator")

    assert excinfo.value.status is ChunkStatus.DELIVERING
    assert excinfo.value.chunk_id == "chk_1"
    assert "delivering" in str(excinfo.value)
    assert "chk_1" in str(excinfo.value)


def test_resume_is_idempotent_on_an_unpaused_chunk() -> None:
    # No refusal at all: resume just appends paused=False, a harmless no-op via
    # newest-fact-wins, matching POST /runners/{id}/resume.
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo(facts=_ready_facts())
    service = PauseService(chunks=_as_write_repo(repo), clock=clock)

    service.resume(_CHUNK, by="operator")

    assert repo.recorded == [("chk_1", False, "operator", _T0)]


def test_resume_is_idempotent_on_a_chunk_with_no_facts_at_all() -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo(facts=None)
    service = PauseService(chunks=_as_write_repo(repo), clock=clock)

    service.resume(_CHUNK, by="operator")

    assert repo.recorded == [("chk_1", False, "operator", _T0)]


def test_set_by_is_carried_onto_the_recorded_fact() -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo(facts=_ready_facts())
    service = PauseService(chunks=_as_write_repo(repo), clock=clock)

    service.pause(_CHUNK, by="paul")

    assert repo.recorded == [("chk_1", True, "paul", _T0)]


def test_pause_uses_the_injected_clock_not_the_wall_clock() -> None:
    later = datetime(2026, 6, 1, tzinfo=UTC)
    clock = FixedClock(instant=later)
    repo = _FakeChunkRepo(facts=_ready_facts())
    service = PauseService(chunks=_as_write_repo(repo), clock=clock)

    service.pause(_CHUNK, by="operator")

    assert repo.recorded == [("chk_1", True, "operator", later)]
