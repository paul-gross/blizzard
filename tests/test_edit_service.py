"""EditService (unit tier) — a chunk's graph/model/intended-migration edit, facts only
(issue #27, #120, #124).

A fake stands in for the store — every unimplemented seam raises loudly if called
(``bzh:domain-core``). The lock's cross-service race atomicity is proven at the
component tier (``tests/test_edit_claim_race.py``), not here."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.edit import (
    UNSET,
    ChunkAlreadyMoved,
    ChunkEdit,
    ChunkNotEditable,
    EditService,
    ForcedNodeUnknown,
    MigrationTargetIsCurrentPin,
    TargetGraphRetired,
)
from blizzard.hub.domain.graph import RESERVED_TERMINAL, IReadGraphRepository, Node
from blizzard.hub.domain.work import (
    Chunk,
    ChunkFacts,
    EscalationFact,
    IntendedMigration,
    IWriteChunkRepository,
    MigrationMode,
    QuestionFact,
    RouteCreatedFact,
    RouteReleasedFact,
    TransitionFact,
)
from tests.support import make_graph

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_CHUNK = Chunk(chunk_id="chk_1", graph_id="gr_1", work_refs=[], minted_at=_T0)
_TARGET_GRAPH = make_graph("gr_2", "alt", entry_node_id="nd_1", created_at=_T0)


def _named_node(node_id: str, name: str) -> Node:
    return Node(
        node_id=node_id,
        graph_id="gr_2",
        name=name,
        executor=Executor.RUNNER,
        prompt="do the work",
        checks=[],
        produces=[],
        session=SessionMode.RESUME,
        judged_by=JudgedBy.WORKER,
        retries_max=None,
        retries_exhausted=None,
        mode=None,
    )


_TARGET_GRAPH_WITH_BUILD = make_graph(
    "gr_2", "alt", entry_node_id="nd_1", nodes=[_named_node("nd_1", "build")], created_at=_T0
)


@dataclass
class _FakeChunkRepo:
    """Only ``load_facts``/``set_graph``/``set_defaults``/``set_intended_migration`` are
    live; anything else is a bug. Not typed against :class:`IWriteChunkRepository`
    directly — callers wrap an instance in :func:`_as_write_repo` instead."""

    facts: ChunkFacts | None
    graphs_set: list[tuple[str, str]] = field(default_factory=list)
    defaults_set: list[tuple[str, list[str], str | None]] = field(default_factory=list)
    intended_migrations_set: list[tuple[str, IntendedMigration | None]] = field(default_factory=list)

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        return self.facts

    def set_graph(self, chunk_id: str, *, graph_id: str) -> None:
        self.graphs_set.append((chunk_id, graph_id))

    def set_defaults(self, chunk_id: str, *, default_model: list[str], default_effort: str | None) -> None:
        self.defaults_set.append((chunk_id, default_model, default_effort))

    def set_intended_migration(self, chunk_id: str, *, intended: IntendedMigration | None) -> None:
        self.intended_migrations_set.append((chunk_id, intended))

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
        chunks=_as_write_repo(repo),
        graphs=_as_read_graph_repo(graphs or _FakeGraphRepo()),
        claim_lock=threading.Lock(),
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


# --- set_graph / set_defaults — thin wrappers over edit(). ---


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


def test_set_defaults_writes_on_a_not_ready_chunk() -> None:
    repo = _FakeChunkRepo(facts=_not_ready_facts())
    service = _service(repo)

    service.set_defaults(_CHUNK, default_model=["blizzard:basic"], default_effort="medium")

    assert repo.defaults_set == [("chk_1", ["blizzard:basic"], "medium")]


def test_set_graph_writes_on_a_ready_unclaimed_chunk() -> None:
    """Issue #120 — a promoted-but-unclaimed chunk is still editable."""
    repo = _FakeChunkRepo(facts=_ready_facts())
    service = _service(repo)

    service.set_graph(_CHUNK, graph=_TARGET_GRAPH)

    assert repo.graphs_set == [("chk_1", "gr_2")]


def test_set_defaults_writes_on_a_ready_unclaimed_chunk() -> None:
    """Issue #120 — a promoted-but-unclaimed chunk is still editable."""
    repo = _FakeChunkRepo(facts=_ready_facts())
    service = _service(repo)

    service.set_defaults(_CHUNK, default_model=["blizzard:basic"], default_effort="medium")

    assert repo.defaults_set == [("chk_1", ["blizzard:basic"], "medium")]


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
def test_set_defaults_refuses_every_status_once_claimed(facts_factory: object) -> None:
    repo = _FakeChunkRepo(facts=facts_factory())  # type: ignore[operator]
    service = _service(repo)

    with pytest.raises(ChunkNotEditable):
        service.set_defaults(_CHUNK, default_model=["blizzard:basic"], default_effort=None)

    assert repo.defaults_set == []


def test_refusal_carries_the_offending_field_and_status_on_the_exception() -> None:
    repo = _FakeChunkRepo(facts=_running_facts())
    service = _service(repo)

    with pytest.raises(ChunkNotEditable) as excinfo:
        service.set_defaults(_CHUNK, default_model=["blizzard:basic"], default_effort=None)

    assert excinfo.value.status is ChunkStatus.RUNNING
    assert excinfo.value.chunk_id == "chk_1"
    assert excinfo.value.field == "default_model"
    assert "running" in str(excinfo.value)
    assert "chk_1" in str(excinfo.value)
    assert "default_model" in str(excinfo.value)


def test_set_graph_holds_the_injected_lock_across_its_check_and_write() -> None:
    """Issue #120 — ``EditService`` must take the lock it was constructed with around
    its whole check-then-act, not a private one, so the composition root can serialize
    it against ``ClaimService``'s own CAS."""
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
    # The write happened inside the acquire/release pair, not before or after it.
    assert repo.graphs_set == [("chk_1", "gr_2")]


def test_set_graph_refuses_a_retired_target_on_an_editable_chunk() -> None:
    repo = _FakeChunkRepo(facts=_ready_facts())
    service = _service(repo, graphs=_FakeGraphRepo(retired=frozenset({"gr_2"})))

    with pytest.raises(TargetGraphRetired) as excinfo:
        service.set_graph(_CHUNK, graph=_TARGET_GRAPH)

    assert excinfo.value.graph_id == "gr_2"
    assert repo.graphs_set == []


def _detached_after_running_facts() -> ChunkFacts:
    """Detached back to ``ready`` mid-graph — route released, still standing on ``nd_9``."""
    return ChunkFacts(
        minted=True,
        promoted=True,
        routes_created=[RouteCreatedFact(created_at=_T0)],
        routes_released=[RouteReleasedFact(released_at=_T0, seq=1)],
        transitions=[
            TransitionFact(to_node_id="nd_9", to_node_executor=Executor.RUNNER, epoch=1, recorded_at=_T0),
        ],
    )


def test_set_graph_refuses_a_ready_chunk_that_has_already_moved() -> None:
    """`bzh:migration-not-transition` — a chunk that has run re-pins only by migration.
    Detaching it returns it to ``ready``, so the status window alone would admit the edit
    and strand the chunk on a node its new pin does not contain."""
    repo = _FakeChunkRepo(facts=_detached_after_running_facts())
    service = _service(repo)

    with pytest.raises(ChunkAlreadyMoved) as excinfo:
        service.set_graph(_CHUNK, graph=_TARGET_GRAPH)

    assert excinfo.value.chunk_id == "chk_1"
    assert repo.graphs_set == []


def test_defaults_stay_editable_on_a_ready_chunk_that_has_already_moved() -> None:
    """The unmoved requirement is the graph pin's alone — a default names no node."""
    repo = _FakeChunkRepo(facts=_detached_after_running_facts())
    service = _service(repo)

    service.set_defaults(_CHUNK, default_model=["blizzard:basic"], default_effort="medium")

    assert repo.defaults_set == [("chk_1", ["blizzard:basic"], "medium")]


def test_set_graph_reports_chunk_not_editable_before_checking_a_retired_target() -> None:
    """The chunk's own editability is the more fundamental refusal — checked first, so
    it is what a caller sees even when the target graph is *also* retired."""
    repo = _FakeChunkRepo(facts=_running_facts())
    service = _service(repo, graphs=_FakeGraphRepo(retired=frozenset({"gr_2"})))

    with pytest.raises(ChunkNotEditable):
        service.set_graph(_CHUNK, graph=_TARGET_GRAPH)

    assert repo.graphs_set == []


# --- edit() — intended_migration's window: any non-terminal status. ---

_MIGRATION_TO_GR2 = IntendedMigration(mode=MigrationMode.AUTO, graph_id="gr_2", node_name=None)


@pytest.mark.parametrize(
    "facts_factory",
    [_not_ready_facts, _ready_facts, _running_facts, _waiting_on_human_facts, _needs_human_facts],
    ids=["not_ready", "ready", "running", "waiting_on_human", "needs_human"],
)
def test_edit_intended_migration_writes_on_every_non_terminal_status(facts_factory: object) -> None:
    repo = _FakeChunkRepo(facts=facts_factory())  # type: ignore[operator]
    service = _service(repo)

    service.edit(_CHUNK, ChunkEdit(intended_migration=_MIGRATION_TO_GR2), migration_target=_TARGET_GRAPH)

    assert repo.intended_migrations_set == [("chk_1", _MIGRATION_TO_GR2)]


@pytest.mark.parametrize("facts_factory", [_stopped_facts, _done_facts], ids=["stopped", "done"])
def test_edit_intended_migration_refuses_a_terminal_status(facts_factory: object) -> None:
    repo = _FakeChunkRepo(facts=facts_factory())  # type: ignore[operator]
    service = _service(repo)

    with pytest.raises(ChunkNotEditable) as excinfo:
        service.edit(_CHUNK, ChunkEdit(intended_migration=_MIGRATION_TO_GR2), migration_target=_TARGET_GRAPH)

    assert excinfo.value.field == "intended_migration"
    assert repo.intended_migrations_set == []


def test_edit_intended_migration_clear_via_null_is_distinct_from_absent() -> None:
    """``None`` clears the intent; leaving the field off ``ChunkEdit`` entirely
    (``UNSET``, the default) leaves it untouched — the two must not collapse."""
    repo = _FakeChunkRepo(facts=_running_facts())
    service = _service(repo)

    service.edit(_CHUNK, ChunkEdit(intended_migration=None))

    assert repo.intended_migrations_set == [("chk_1", None)]


def test_edit_with_no_intended_migration_field_at_all_leaves_it_untouched() -> None:
    repo = _FakeChunkRepo(facts=_ready_facts())
    service = _service(repo)

    service.edit(_CHUNK, ChunkEdit(default_model=["blizzard:basic"]))

    assert repo.intended_migrations_set == []
    # `default_effort` was not supplied, so the write carries its current value.
    assert repo.defaults_set == [("chk_1", ["blizzard:basic"], None)]
    # graph_id was never supplied — confirms UNSET, not just "no migration field".
    assert ChunkEdit().graph_id is UNSET


# --- edit() — the semantic refusals for a non-None intended migration. ---


def test_edit_intended_migration_refuses_a_retired_target() -> None:
    repo = _FakeChunkRepo(facts=_running_facts())
    service = _service(repo, graphs=_FakeGraphRepo(retired=frozenset({"gr_2"})))

    with pytest.raises(TargetGraphRetired) as excinfo:
        service.edit(_CHUNK, ChunkEdit(intended_migration=_MIGRATION_TO_GR2), migration_target=_TARGET_GRAPH)

    assert excinfo.value.graph_id == "gr_2"
    assert repo.intended_migrations_set == []


def test_edit_intended_migration_refuses_a_target_equal_to_the_current_pin() -> None:
    repo = _FakeChunkRepo(facts=_running_facts())
    service = _service(repo)
    current_pin_graph = make_graph(_CHUNK.graph_id, "current", created_at=_T0)
    intent = IntendedMigration(mode=MigrationMode.AUTO, graph_id=_CHUNK.graph_id, node_name=None)

    with pytest.raises(MigrationTargetIsCurrentPin) as excinfo:
        service.edit(_CHUNK, ChunkEdit(intended_migration=intent), migration_target=current_pin_graph)

    assert excinfo.value.graph_id == _CHUNK.graph_id
    assert repo.intended_migrations_set == []


def test_edit_intended_migration_forced_refuses_a_node_absent_from_the_target() -> None:
    repo = _FakeChunkRepo(facts=_running_facts())
    service = _service(repo)
    intent = IntendedMigration(mode=MigrationMode.FORCED, graph_id="gr_2", node_name="nope")

    with pytest.raises(ForcedNodeUnknown) as excinfo:
        service.edit(_CHUNK, ChunkEdit(intended_migration=intent), migration_target=_TARGET_GRAPH_WITH_BUILD)

    assert excinfo.value.node_name == "nope"
    assert excinfo.value.graph_id == "gr_2"
    assert repo.intended_migrations_set == []


def test_edit_intended_migration_forced_writes_when_the_node_exists_on_the_target() -> None:
    repo = _FakeChunkRepo(facts=_running_facts())
    service = _service(repo)
    intent = IntendedMigration(mode=MigrationMode.FORCED, graph_id="gr_2", node_name="build")

    service.edit(_CHUNK, ChunkEdit(intended_migration=intent), migration_target=_TARGET_GRAPH_WITH_BUILD)

    assert repo.intended_migrations_set == [("chk_1", intent)]


def test_edit_intended_migration_auto_does_not_check_node_names() -> None:
    """``auto`` carries no ``node_name`` — the forced-only node lookup never fires."""
    repo = _FakeChunkRepo(facts=_running_facts())
    service = _service(repo)

    service.edit(_CHUNK, ChunkEdit(intended_migration=_MIGRATION_TO_GR2), migration_target=_TARGET_GRAPH)

    assert repo.intended_migrations_set == [("chk_1", _MIGRATION_TO_GR2)]


def test_edit_graph_id_retirement_check_is_not_bypassed_by_a_different_migration_target() -> None:
    """A retired ``graph_id`` target must not slip past its :class:`TargetGraphRetired`
    check just because the request's ``intended_migration`` names a different,
    non-retired graph (issue #124)."""
    repo = _FakeChunkRepo(facts=_ready_facts())
    migration_graph = make_graph("gr_3", "other", entry_node_id="nd_1", created_at=_T0)
    service = _service(repo, graphs=_FakeGraphRepo(retired=frozenset({"gr_2"})))
    intent = IntendedMigration(mode=MigrationMode.AUTO, graph_id="gr_3", node_name=None)

    with pytest.raises(TargetGraphRetired) as excinfo:
        service.edit(
            _CHUNK,
            ChunkEdit(graph_id="gr_2", intended_migration=intent),
            graph_target=_TARGET_GRAPH,
            migration_target=migration_graph,
        )

    assert excinfo.value.graph_id == "gr_2"
    assert repo.graphs_set == []
    assert repo.intended_migrations_set == []


# --- edit() — mixed and all-editable bodies, all-or-nothing. ---


def test_edit_applies_every_supplied_field_in_one_edit() -> None:
    repo = _FakeChunkRepo(facts=_ready_facts())
    service = _service(repo)

    service.edit(
        _CHUNK,
        ChunkEdit(graph_id="gr_2", default_model=["blizzard:basic"], default_effort="high"),
        graph_target=_TARGET_GRAPH,
    )

    assert repo.graphs_set == [("chk_1", "gr_2")]
    assert repo.defaults_set == [("chk_1", ["blizzard:basic"], "high")]


def test_edit_refuses_a_mixed_body_on_one_field_and_writes_nothing() -> None:
    """``default_model`` is editable only pre-claim; a running chunk's
    ``intended_migration`` is editable, but the whole body is refused (and nothing
    written) because ``default_model`` isn't — named on the exception."""
    repo = _FakeChunkRepo(facts=_running_facts())
    service = _service(repo)

    with pytest.raises(ChunkNotEditable) as excinfo:
        service.edit(
            _CHUNK,
            ChunkEdit(default_model=["blizzard:basic"], intended_migration=_MIGRATION_TO_GR2),
            migration_target=_TARGET_GRAPH,
        )

    assert excinfo.value.field == "default_model"
    assert repo.defaults_set == []
    assert repo.intended_migrations_set == []


def test_edit_with_an_empty_chunk_edit_writes_nothing() -> None:
    repo = _FakeChunkRepo(facts=_running_facts())
    service = _service(repo)

    service.edit(_CHUNK, ChunkEdit())

    assert repo.graphs_set == []
    assert repo.defaults_set == []
    assert repo.intended_migrations_set == []
