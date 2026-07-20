"""EditService (unit tier) — a not-ready or ready-unclaimed chunk's graph/model edit,
facts only (issue #27, admit set widened by #120).

A fake stands in for the store — only ``load_facts``/``set_graph``/``set_model`` are
meaningfully implemented; every other seam is unreachable from
:meth:`EditService.set_graph`/``set_model`` and raises loudly if a regression starts
calling it (``bzh:domain-core`` — no store, no tokens). Copies
:mod:`tests.test_pause_service`'s fake-repo pattern exactly, including its
``__getattr__`` guard and the documented ``cast`` at the wide-Protocol call site
(``bzh:repository-split``). Every service under test here is built with a fresh
``threading.Lock()`` — a plain stand-in for the composition root's shared claim/edit
lock (issue #120); the lock's cross-service race atomicity is proven at the component
tier (``tests/test_edit_claim_race.py``), not here.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.hub.domain.edit import ChunkNotEditable, EditService, TargetGraphRetired
from blizzard.hub.domain.graph import RESERVED_TERMINAL, Executor, IReadGraphRepository
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
from tests.support import make_graph

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_CHUNK = Chunk(chunk_id="chk_1", graph_id="gr_1", pm_pointers=[], minted_at=_T0, model="claude-opus-4-8")
_TARGET_GRAPH = make_graph("gr_2", "alt", entry_node_id="nd_1", created_at=_T0)


@dataclass
class _FakeChunkRepo:
    """Only ``load_facts``/``set_graph``/``set_model`` are live; anything else is a bug.

    Not typed against :class:`IWriteChunkRepository` directly — pyright cannot verify
    ``__getattr__``-backed structural conformance, so callers wrap an instance in
    :func:`_as_write_repo` instead."""

    facts: ChunkFacts | None
    graphs_set: list[tuple[str, str]] = field(default_factory=list)
    models_set: list[tuple[str, str]] = field(default_factory=list)

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        return self.facts

    def set_graph(self, chunk_id: str, *, graph_id: str) -> None:
        self.graphs_set.append((chunk_id, graph_id))

    def set_model(self, chunk_id: str, *, model: str) -> None:
        self.models_set.append((chunk_id, model))

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"EditService should not touch {name!r}")


def _as_write_repo(repo: _FakeChunkRepo) -> IWriteChunkRepository:
    """Assert the fake satisfies the Protocol EditService depends on (see module docstring)."""
    return cast(IWriteChunkRepository, repo)


@dataclass
class _FakeGraphRepo:
    """Only ``is_retired`` is live; anything else is a bug (see module docstring)."""

    retired: frozenset[str] = frozenset()

    def is_retired(self, graph_id: str) -> bool:
        return graph_id in self.retired

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"EditService should not touch {name!r}")


def _as_read_graph_repo(repo: _FakeGraphRepo) -> IReadGraphRepository:
    """Assert the fake satisfies the Protocol EditService depends on (see module docstring)."""
    return cast(IReadGraphRepository, repo)


def _service(repo: _FakeChunkRepo, graphs: _FakeGraphRepo | None = None) -> EditService:
    """Build an ``EditService`` over ``repo`` with a fresh, single-test claim lock
    (see module docstring — the shared-lock race is proven at the component tier).
    ``graphs`` defaults to a fake reporting no graph retired."""
    return EditService(
        chunks=_as_write_repo(repo), graphs=_as_read_graph_repo(graphs or _FakeGraphRepo()), claim_lock=threading.Lock()
    )


def _not_ready_facts() -> ChunkFacts:
    return ChunkFacts(minted=True)


def _ready_facts() -> ChunkFacts:
    return ChunkFacts(minted=True, promoted=True)


def _running_facts() -> ChunkFacts:
    return ChunkFacts(minted=True, promoted=True, routes_created=[RouteCreatedFact(created_at=_T0)])


def _waiting_on_human_facts() -> ChunkFacts:
    return ChunkFacts(
        minted=True,
        promoted=True,
        routes_created=[RouteCreatedFact(created_at=_T0)],
        questions=[QuestionFact(question_id="qn_1", asked_at=_T0, answered=False)],
    )


def _needs_human_facts() -> ChunkFacts:
    return ChunkFacts(
        minted=True,
        promoted=True,
        routes_created=[RouteCreatedFact(created_at=_T0)],
        escalations=[EscalationFact(epoch=1, recorded_at=_T0)],
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


def test_set_graph_writes_on_a_not_ready_chunk() -> None:
    repo = _FakeChunkRepo(facts=_not_ready_facts())
    service = _service(repo)

    service.set_graph(_CHUNK, graph=_TARGET_GRAPH)

    assert repo.graphs_set == [("chk_1", "gr_2")]


def test_set_graph_on_a_chunk_with_no_facts_at_all_is_not_ready_and_writes() -> None:
    # A freshly minted, un-hydrated chunk (no store row loaded yet) derives not_ready.
    repo = _FakeChunkRepo(facts=None)
    service = _service(repo)

    service.set_graph(_CHUNK, graph=_TARGET_GRAPH)

    assert repo.graphs_set == [("chk_1", "gr_2")]


def test_set_model_writes_on_a_not_ready_chunk() -> None:
    repo = _FakeChunkRepo(facts=_not_ready_facts())
    service = _service(repo)

    service.set_model(_CHUNK, model="claude-sonnet-4-5")

    assert repo.models_set == [("chk_1", "claude-sonnet-4-5")]


def test_set_graph_writes_on_a_ready_unclaimed_chunk() -> None:
    """Issue #120 — a promoted-but-unclaimed chunk is still editable."""
    repo = _FakeChunkRepo(facts=_ready_facts())
    service = _service(repo)

    service.set_graph(_CHUNK, graph=_TARGET_GRAPH)

    assert repo.graphs_set == [("chk_1", "gr_2")]


def test_set_model_writes_on_a_ready_unclaimed_chunk() -> None:
    """Issue #120 — a promoted-but-unclaimed chunk is still editable."""
    repo = _FakeChunkRepo(facts=_ready_facts())
    service = _service(repo)

    service.set_model(_CHUNK, model="claude-sonnet-4-5")

    assert repo.models_set == [("chk_1", "claude-sonnet-4-5")]


@pytest.mark.parametrize(
    "facts_factory",
    [_running_facts, _waiting_on_human_facts, _needs_human_facts, _stopped_facts, _done_facts],
    ids=["running", "waiting_on_human", "needs_human", "stopped", "done"],
)
def test_set_graph_refuses_every_status_once_claimed(facts_factory: object) -> None:
    repo = _FakeChunkRepo(facts=facts_factory())  # type: ignore[operator]
    service = _service(repo)

    with pytest.raises(ChunkNotEditable):
        service.set_graph(_CHUNK, graph=_TARGET_GRAPH)

    assert repo.graphs_set == []


@pytest.mark.parametrize(
    "facts_factory",
    [_running_facts, _waiting_on_human_facts, _needs_human_facts, _stopped_facts, _done_facts],
    ids=["running", "waiting_on_human", "needs_human", "stopped", "done"],
)
def test_set_model_refuses_every_status_once_claimed(facts_factory: object) -> None:
    repo = _FakeChunkRepo(facts=facts_factory())  # type: ignore[operator]
    service = _service(repo)

    with pytest.raises(ChunkNotEditable):
        service.set_model(_CHUNK, model="claude-sonnet-4-5")

    assert repo.models_set == []


def test_refusal_carries_the_offending_status_on_the_exception() -> None:
    repo = _FakeChunkRepo(facts=_running_facts())
    service = _service(repo)

    with pytest.raises(ChunkNotEditable) as excinfo:
        service.set_model(_CHUNK, model="claude-sonnet-4-5")

    assert excinfo.value.status is ChunkStatus.RUNNING
    assert excinfo.value.chunk_id == "chk_1"
    assert "running" in str(excinfo.value)
    assert "chk_1" in str(excinfo.value)


def test_set_graph_holds_the_injected_lock_across_its_check_and_write() -> None:
    """Issue #120 — ``EditService`` must take **the lock it was constructed with**
    around its whole check-then-act, not a lock of its own: this is what lets the
    composition root serialize it against ``ClaimService``'s own CAS. A blocking-only
    fake lock proves the service actually calls through to the injected object rather
    than a private ``threading.Lock()`` it happens to also hold uncontended."""
    repo = _FakeChunkRepo(facts=_ready_facts())
    calls: list[str] = []

    class _SpyLock:
        def __enter__(self) -> None:
            calls.append("acquire")

        def __exit__(self, *exc: object) -> None:
            calls.append("release")

    service = EditService(
        chunks=_as_write_repo(repo),
        graphs=_as_read_graph_repo(_FakeGraphRepo()),
        claim_lock=cast(threading.Lock, _SpyLock()),
    )

    service.set_graph(_CHUNK, graph=_TARGET_GRAPH)

    assert calls == ["acquire", "release"]
    # The write happened while the caller believes the lock is held — i.e. inside the
    # acquire/release pair, not before or after it (proven above by call order alone,
    # since the fake repo's write is synchronous and calls are appended in program order).
    assert repo.graphs_set == [("chk_1", "gr_2")]


def test_set_graph_refuses_a_retired_target_on_an_editable_chunk() -> None:
    repo = _FakeChunkRepo(facts=_ready_facts())
    service = _service(repo, graphs=_FakeGraphRepo(retired=frozenset({"gr_2"})))

    with pytest.raises(TargetGraphRetired) as excinfo:
        service.set_graph(_CHUNK, graph=_TARGET_GRAPH)

    assert excinfo.value.graph_id == "gr_2"
    assert repo.graphs_set == []


def test_set_graph_reports_chunk_not_editable_before_checking_a_retired_target() -> None:
    """The chunk's own editability is the more fundamental refusal — checked first, so
    it is what a caller sees even when the target graph is *also* retired."""
    repo = _FakeChunkRepo(facts=_running_facts())
    service = _service(repo, graphs=_FakeGraphRepo(retired=frozenset({"gr_2"})))

    with pytest.raises(ChunkNotEditable):
        service.set_graph(_CHUNK, graph=_TARGET_GRAPH)

    assert repo.graphs_set == []
