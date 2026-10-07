"""The operator verbs' rules (unit tier) — which statuses admit each verb, and the pure
decisions promote, pause, stop, complete, restart, delete, and edit take over loaded facts,
pinned by value: no repository, no clock."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.chunk_migration import MigrationMode
from blizzard.foundation.chunk_status import PRE_CLAIM_STATUSES, TERMINAL_STATUSES, ChunkStatus
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.chunk.model import (
    CHUNK_VERB_LEGALITY,
    Chunk,
    ChunkFacts,
    ChunkVerb,
    DecisionFact,
    DependencyEdge,
    EscalationFact,
    IntendedMigration,
    PauseFact,
    QuestionFact,
    RestartFact,
    RouteCreatedFact,
    TransitionFact,
)
from blizzard.hub.domain.graph.harnesses import InvalidHarnesses
from blizzard.hub.domain.graph.model import Node, TargetGraphRetired
from blizzard.hub.domain.kernel.unset import UNSET
from blizzard.hub.domain.operations.complete import is_completion_replay
from blizzard.hub.domain.operations.delete import (
    ChunkHasDependents,
    ChunkNotDeletable,
    require_deletable,
    require_no_dependents,
)
from blizzard.hub.domain.operations.edit import (
    ChunkAlreadyMoved,
    ChunkDefaults,
    ChunkEdit,
    ChunkNotEditable,
    ForcedNodeUnknown,
    MigrationTargetIsCurrentPin,
    plan_edit,
)
from blizzard.hub.domain.operations.pause import ChunkNotPausable, require_pausable
from blizzard.hub.domain.operations.promote import ChunkNotPromotable, promotion_writes, withheld_by_pause
from blizzard.hub.domain.operations.restart import (
    SUPERSEDED_ANSWER,
    ChunkNotRestartable,
    RestartCurrentNodeUnknown,
    RestartGraphPinChanged,
    RestartNodeUnknown,
    RestartPlan,
    plan_restart,
    restart_landing,
)
from blizzard.hub.domain.operations.stop import ChunkNotStoppable, require_stoppable
from tests.support import make_graph

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_NON_TERMINAL = frozenset(ChunkStatus) - TERMINAL_STATUSES


def _node(node_id: str, name: str, *, graph_id: str, executor: Executor = Executor.RUNNER) -> Node:
    return Node(
        node_id=node_id,
        graph_id=graph_id,
        name=name,
        executor=executor,
        prompt=None,
        checks=[],
        produces=[],
        session=SessionMode.FRESH,
        judged_by=JudgedBy.WORKER,
        retries_max=None,
        retries_exhausted=None,
    )


_GRAPH = make_graph(
    "gr_1",
    "main",
    entry_node_id="nd_plan",
    nodes=[_node("nd_plan", "plan", graph_id="gr_1"), _node("nd_build", "build", graph_id="gr_1")],
)
_OTHER = make_graph(
    "gr_2",
    "other",
    entry_node_id="nd_intake",
    nodes=[_node("nd_intake", "intake", graph_id="gr_2"), _node("nd_build2", "build", graph_id="gr_2")],
)
_CHUNK = Chunk(chunk_id="chk_1", graph_id="gr_1", work_refs=[], minted_at=_T0, default_model=["opus"])

#: One loaded fact set per derived status — every verb is judged against each.
_AT: dict[ChunkStatus, ChunkFacts] = {
    ChunkStatus.NOT_READY: ChunkFacts(minted=True),
    ChunkStatus.READY: ChunkFacts(minted=True, promoted=True),
    ChunkStatus.RUNNING: ChunkFacts(minted=True, promoted=True, routes_created=[RouteCreatedFact(created_at=_T0)]),
    ChunkStatus.DELIVERING: ChunkFacts(
        minted=True,
        promoted=True,
        transitions=[TransitionFact(to_node_id="nd_hub", to_node_executor=Executor.HUB, epoch=1, recorded_at=_T0)],
    ),
    ChunkStatus.PAUSED: ChunkFacts(
        minted=True, promoted=True, pauses=[PauseFact(paused=True, set_at=_T0, set_by="op")]
    ),
    ChunkStatus.WAITING_ON_HUMAN: ChunkFacts(minted=True, promoted=True, questions=[QuestionFact("q1", _T0)]),
    ChunkStatus.NEEDS_HUMAN: ChunkFacts(
        minted=True, promoted=True, escalations=[EscalationFact(epoch=1, recorded_at=_T0)]
    ),
    ChunkStatus.STOPPED: ChunkFacts(minted=True, stopped=True, stopped_at=_T0),
    ChunkStatus.DONE: ChunkFacts(minted=True, operator_completed=True, operator_completed_at=_T0),
}


def test_each_fixture_derives_the_status_it_is_keyed_by() -> None:
    assert {status: facts.status() for status, facts in _AT.items()} == {status: status for status in _AT}


# --- The verb legality table --------------------------------------------------


@pytest.mark.parametrize(
    ("verb", "legal"),
    [
        (ChunkVerb.PROMOTE, _NON_TERMINAL),
        (ChunkVerb.PAUSE, _NON_TERMINAL - {ChunkStatus.DELIVERING}),
        (ChunkVerb.RESUME, frozenset(ChunkStatus)),
        (ChunkVerb.STOP, _NON_TERMINAL),
        (ChunkVerb.COMPLETE, frozenset(ChunkStatus)),
        (ChunkVerb.RESTART, _NON_TERMINAL),
        (ChunkVerb.DELETE, PRE_CLAIM_STATUSES),
        (ChunkVerb.GROUP, PRE_CLAIM_STATUSES),
        (ChunkVerb.EDIT_GRAPH_PIN, PRE_CLAIM_STATUSES),
        (ChunkVerb.EDIT_DEFAULTS, PRE_CLAIM_STATUSES),
        (ChunkVerb.EDIT_INTENDED_MIGRATION, _NON_TERMINAL),
        (ChunkVerb.REORDER_READY, frozenset({ChunkStatus.READY})),
        (ChunkVerb.REORDER_BACKLOG, frozenset({ChunkStatus.NOT_READY})),
    ],
)
def test_operator_verb_is_legal_from_exactly_its_declared_statuses(
    verb: ChunkVerb, legal: frozenset[ChunkStatus]
) -> None:
    assert CHUNK_VERB_LEGALITY[verb] == legal
    assert {s for s, facts in _AT.items() if facts.admits(verb)} == set(legal)


# --- Status: a restart never promotes ----------------------------------------


def _restarted_onto_hub_node(*, promoted: bool) -> ChunkFacts:
    restart = RestartFact(
        to_node_id="nd_hub", from_node_id=None, graph_id="gr_1", epoch=1, recorded_at=_T0, to_node_executor=Executor.HUB
    )
    return ChunkFacts(minted=True, promoted=promoted, restarts=[restart])


def test_a_never_promoted_chunk_restarted_onto_a_hub_node_stays_not_ready() -> None:
    assert _restarted_onto_hub_node(promoted=False).status() is ChunkStatus.NOT_READY


def test_a_promoted_chunk_restarted_onto_a_hub_node_derives_delivering() -> None:
    assert _restarted_onto_hub_node(promoted=True).status() is ChunkStatus.DELIVERING


# --- promote ------------------------------------------------------------------


def test_promote_writes_for_a_never_promoted_chunk_at_not_ready() -> None:
    assert promotion_writes("chk_1", _AT[ChunkStatus.NOT_READY]) is True


def test_promote_writes_for_a_never_promoted_paused_chunk() -> None:
    paused = ChunkFacts(minted=True, pauses=[PauseFact(paused=True, set_at=_T0, set_by="op")])
    assert paused.status() is ChunkStatus.PAUSED
    assert promotion_writes("chk_1", paused) is True


@pytest.mark.parametrize("status", sorted(TERMINAL_STATUSES))
def test_promote_refuses_a_never_promoted_terminal_chunk(status: ChunkStatus) -> None:
    with pytest.raises(ChunkNotPromotable) as excinfo:
        promotion_writes("chk_1", _AT[status])
    assert excinfo.value.status is status
    assert str(excinfo.value) == f"chunk chk_1 is {status.value}, not promotable"


def test_promote_replays_on_an_already_promoted_chunk_even_once_terminal() -> None:
    promoted_and_done = ChunkFacts(minted=True, promoted=True, operator_completed=True, operator_completed_at=_T0)
    assert promotion_writes("chk_1", promoted_and_done) is False
    assert promotion_writes("chk_1", _AT[ChunkStatus.RUNNING]) is False


def test_only_the_paused_chunks_a_pause_alone_withholds_rank_with_the_queue() -> None:
    held = ChunkFacts(minted=True, promoted=True, pauses=[PauseFact(paused=True, set_at=_T0, set_by="op")])
    unpromoted = ChunkFacts(minted=True, pauses=[PauseFact(paused=True, set_at=_T0, set_by="op")])
    assert withheld_by_pause({"chk_held": held, "chk_unpromoted": unpromoted}) == ["chk_held"]


# --- pause / stop / complete --------------------------------------------------


@pytest.mark.parametrize("status", [ChunkStatus.DONE, ChunkStatus.STOPPED, ChunkStatus.DELIVERING])
def test_pause_refuses_done_stopped_and_delivering(status: ChunkStatus) -> None:
    with pytest.raises(ChunkNotPausable) as excinfo:
        require_pausable("chk_1", _AT[status])
    assert excinfo.value.status is status


def test_status_if_paused_settles_paused_unless_refused_or_outranked_by_a_human_gate() -> None:
    assert {status: facts.status_if_paused() for status, facts in _AT.items()} == {
        ChunkStatus.NOT_READY: ChunkStatus.PAUSED,
        ChunkStatus.READY: ChunkStatus.PAUSED,
        ChunkStatus.RUNNING: ChunkStatus.PAUSED,
        ChunkStatus.DELIVERING: ChunkStatus.DELIVERING,
        ChunkStatus.PAUSED: ChunkStatus.PAUSED,
        ChunkStatus.WAITING_ON_HUMAN: ChunkStatus.WAITING_ON_HUMAN,
        ChunkStatus.NEEDS_HUMAN: ChunkStatus.NEEDS_HUMAN,
        ChunkStatus.STOPPED: ChunkStatus.STOPPED,
        ChunkStatus.DONE: ChunkStatus.DONE,
    }


def test_status_if_paused_predicts_a_re_pause_of_a_resumed_chunk() -> None:
    # Newest-fact-wins: a resume ends the earlier pause, and a fresh pause re-derives ``paused``.
    resumed = ChunkFacts(
        minted=True,
        promoted=True,
        pauses=[
            PauseFact(paused=True, set_at=_T0, set_by="op"),
            PauseFact(paused=False, set_at=_T0, set_by="op"),
        ],
    )
    assert resumed.status() is ChunkStatus.READY
    assert resumed.status_if_paused() is ChunkStatus.PAUSED


def test_pause_is_a_brake_not_a_fence_an_open_decision_does_not_refuse_it() -> None:
    gated = ChunkFacts(minted=True, promoted=True, decisions=[DecisionFact(decision_id="d1", submitted_at=_T0)])
    assert gated.status() is ChunkStatus.WAITING_ON_HUMAN
    require_pausable("chk_1", gated)


@pytest.mark.parametrize("status", sorted(TERMINAL_STATUSES))
def test_stop_refuses_a_terminal_chunk(status: ChunkStatus) -> None:
    with pytest.raises(ChunkNotStoppable):
        require_stoppable("chk_1", _AT[status])


def test_completion_replays_only_at_done_and_writes_from_stopped() -> None:
    assert {s for s, facts in _AT.items() if is_completion_replay(facts)} == {ChunkStatus.DONE}


# --- delete -------------------------------------------------------------------


@pytest.mark.parametrize("status", sorted(frozenset(ChunkStatus) - PRE_CLAIM_STATUSES))
def test_delete_refuses_outside_the_pre_claim_window(status: ChunkStatus) -> None:
    with pytest.raises(ChunkNotDeletable):
        require_deletable("chk_1", _AT[status])


def _edge(dependent: str, prerequisite: str, *, released: bool = False) -> DependencyEdge:
    return DependencyEdge(
        dependency_id=f"dep_{dependent}_{prerequisite}",
        dependent_chunk_id=dependent,
        prerequisite_chunk_id=prerequisite,
        declared_at=_T0,
        declared_by="op",
        released_at=_T0 if released else None,
    )


def test_delete_refuses_a_standing_prerequisite_naming_its_dependents_sorted() -> None:
    with pytest.raises(ChunkHasDependents) as excinfo:
        require_no_dependents("chk_1", [_edge("chk_c", "chk_1"), _edge("chk_a", "chk_1"), _edge("chk_1", "chk_z")])
    assert excinfo.value.dependent_chunk_ids == ["chk_a", "chk_c"]


def test_delete_is_free_of_edges_where_the_chunk_is_only_a_dependent() -> None:
    require_no_dependents("chk_1", [_edge("chk_1", "chk_z")])


# --- restart ------------------------------------------------------------------


def test_restart_landing_resolves_a_named_node_on_the_landing_graph() -> None:
    assert restart_landing(_GRAPH, _OTHER, "nd_plan", "intake").node_id == "nd_intake"


def test_restart_landing_defaults_a_never_moved_chunk_to_the_landing_entry() -> None:
    assert restart_landing(_GRAPH, None, None, None).node_id == "nd_plan"
    assert restart_landing(_GRAPH, _OTHER, None, None).node_id == "nd_intake"


def test_restart_landing_name_matches_the_current_node_across_graphs() -> None:
    assert restart_landing(_GRAPH, None, "nd_build", None).node_id == "nd_build"
    assert restart_landing(_GRAPH, _OTHER, "nd_build", None).node_id == "nd_build2"


def test_restart_landing_refuses_an_unknown_name_and_an_unmatched_current_node() -> None:
    with pytest.raises(RestartNodeUnknown):
        restart_landing(_GRAPH, None, None, "nope")
    with pytest.raises(RestartNodeUnknown):
        restart_landing(_GRAPH, _OTHER, "nd_plan", None)
    with pytest.raises(RestartCurrentNodeUnknown):
        restart_landing(_GRAPH, None, "nd_gone", None)


def test_plan_restart_consumes_the_open_gate_and_asks_and_records_the_re_pin() -> None:
    facts = ChunkFacts(
        minted=True,
        promoted=True,
        questions=[QuestionFact("q1", _T0), QuestionFact("q2", _T0, answered=True)],
        decisions=[DecisionFact(decision_id="d1", submitted_at=_T0)],
    )
    plan = plan_restart(_CHUNK, facts, _GRAPH, node_name="build", to_graph=_OTHER)
    assert plan == RestartPlan(
        from_node_id=None,
        to_node_id="nd_build2",
        decision_id="d1",
        answered_question_ids=["q1"],
        answer=SUPERSEDED_ANSWER,
        to_graph_id="gr_2",
    )


@pytest.mark.parametrize("status", sorted(TERMINAL_STATUSES))
def test_plan_restart_refuses_a_terminal_chunk(status: ChunkStatus) -> None:
    with pytest.raises(ChunkNotRestartable):
        plan_restart(_CHUNK, _AT[status], _GRAPH, node_name=None)


def test_plan_restart_refuses_a_retired_or_current_pin_target_and_a_stale_graph() -> None:
    ready = _AT[ChunkStatus.READY]
    with pytest.raises(TargetGraphRetired):
        plan_restart(_CHUNK, ready, _GRAPH, node_name=None, to_graph=_OTHER, to_graph_retired=True)
    with pytest.raises(MigrationTargetIsCurrentPin):
        plan_restart(_CHUNK, ready, _GRAPH, node_name=None, to_graph=_GRAPH)
    with pytest.raises(RestartGraphPinChanged):
        plan_restart(_CHUNK, ready, _OTHER, node_name=None)


# --- edit ---------------------------------------------------------------------


def test_edit_naming_the_current_pin_plans_no_pin_write() -> None:
    plan = plan_edit(_CHUNK, _AT[ChunkStatus.READY], ChunkEdit(graph_id="gr_1"), graph_target=_GRAPH)
    assert plan.graph_id is None


def test_edit_naming_a_since_retired_current_pin_is_a_no_op_and_applies_the_defaults() -> None:
    plan = plan_edit(
        _CHUNK,
        _AT[ChunkStatus.READY],
        ChunkEdit(graph_id="gr_1", default_effort="high"),
        graph_target=_GRAPH,
        graph_target_retired=True,
    )
    assert plan.graph_id is None
    assert plan.defaults is not None and plan.defaults.effort == "high"


def test_edit_naming_another_pin_plans_its_write() -> None:
    plan = plan_edit(_CHUNK, _AT[ChunkStatus.READY], ChunkEdit(graph_id="gr_2"), graph_target=_OTHER)
    assert plan.graph_id == "gr_2"


def test_a_pin_edit_without_its_loaded_target_graph_is_refused() -> None:
    with pytest.raises(ValueError, match="requires its loaded target graph"):
        plan_edit(_CHUNK, _AT[ChunkStatus.READY], ChunkEdit(graph_id="gr_2"))
    with pytest.raises(ValueError, match="requires its loaded target graph"):
        plan_edit(_CHUNK, _AT[ChunkStatus.READY], ChunkEdit(graph_id="gr_2"), graph_target=_GRAPH)


def test_a_pin_edit_refuses_a_moved_chunk_and_a_retired_target() -> None:
    moved = ChunkFacts(
        minted=True,
        transitions=[TransitionFact(to_node_id="nd_build", to_node_executor=Executor.RUNNER, epoch=1, recorded_at=_T0)],
    )
    with pytest.raises(ChunkAlreadyMoved):
        plan_edit(_CHUNK, moved, ChunkEdit(graph_id="gr_2"), graph_target=_OTHER)
    with pytest.raises(TargetGraphRetired):
        plan_edit(
            _CHUNK, _AT[ChunkStatus.READY], ChunkEdit(graph_id="gr_2"), graph_target=_OTHER, graph_target_retired=True
        )


def test_edit_refuses_a_field_outside_its_own_window_naming_the_field() -> None:
    with pytest.raises(ChunkNotEditable) as excinfo:
        plan_edit(_CHUNK, _AT[ChunkStatus.RUNNING], ChunkEdit(default_effort="high"))
    assert excinfo.value.field == "default_effort"
    with pytest.raises(ChunkNotEditable) as excinfo:
        plan_edit(_CHUNK, _AT[ChunkStatus.STOPPED], ChunkEdit(intended_migration=None))
    assert excinfo.value.field == "intended_migration"


def test_edit_carries_the_unsupplied_defaults_forward_from_the_current_record() -> None:
    plan = plan_edit(_CHUNK, _AT[ChunkStatus.READY], ChunkEdit(default_effort="high"))
    assert plan.defaults == ChunkDefaults(model=["opus"], effort="high", harnesses=[])
    assert plan.intended_migration is UNSET


def test_edit_validates_the_harness_list() -> None:
    with pytest.raises(InvalidHarnesses):
        plan_edit(_CHUNK, _AT[ChunkStatus.READY], ChunkEdit(default_harnesses=["", ""]))


def test_an_intended_migration_is_settable_while_running_and_its_target_is_checked() -> None:
    running = _AT[ChunkStatus.RUNNING]
    auto = IntendedMigration.toward("gr_2", None)
    assert (
        plan_edit(_CHUNK, running, ChunkEdit(intended_migration=auto), migration_target=_OTHER).intended_migration
        == auto
    )
    with pytest.raises(TargetGraphRetired):
        plan_edit(
            _CHUNK, running, ChunkEdit(intended_migration=auto), migration_target=_OTHER, migration_target_retired=True
        )
    with pytest.raises(MigrationTargetIsCurrentPin):
        plan_edit(
            _CHUNK,
            running,
            ChunkEdit(intended_migration=IntendedMigration.toward("gr_1", None)),
            migration_target=_GRAPH,
        )
    with pytest.raises(ForcedNodeUnknown):
        plan_edit(
            _CHUNK,
            running,
            ChunkEdit(intended_migration=IntendedMigration.toward("gr_2", "nope")),
            migration_target=_OTHER,
        )


def test_an_intended_migration_mode_is_whether_a_node_is_named() -> None:
    assert IntendedMigration.toward("gr_2", None) == IntendedMigration(MigrationMode.AUTO, "gr_2", None)
    assert IntendedMigration.toward("gr_2", "build") == IntendedMigration(MigrationMode.FORCED, "gr_2", "build")
