"""Completion rules, pinned by value — no repository, no clock.

The attempt-coherence refusals, the plan a plain or resolving completion takes, the migration
replay classification, the migration targets and landing consult, the next step after a
transition, and the artifact rows a submission lands."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TypedDict, Unpack

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.foundation.migration_source import MigrationSource
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.chunk.model import (
    Chunk,
    ChunkFacts,
    DecisionChoice,
    EscalationFact,
    GateDecision,
    IntendedMigration,
    LeaseFact,
    MigrationFact,
    QuestionFact,
    TransitionFact,
)
from blizzard.hub.domain.execution.apply import ApplyResult
from blizzard.hub.domain.execution.completion import (
    ChunkEscalated,
    CompletionNotAtCurrentNode,
    CompletionPlan,
    CompletionRefused,
    GateDecisionOpen,
    GateResolutionUnapplied,
    HubExecutedNode,
    Landing,
    MigrationTargets,
    NextStep,
    NextStepKind,
    OpenQuestion,
    ReplayedMigration,
    decision_choices,
    gate_refusal,
    refuse_hub_executed,
    refuse_incoherent_attempt,
    replayed_migration,
    stored_artifacts,
)
from blizzard.hub.domain.execution.submissions import CheckOutcome, Completion, CompletionArtifact
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL, Choice, Edge, FollowLatest, Node, ProducesSpec
from tests.support import make_graph

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = _T0 + timedelta(minutes=1)


def _node(
    node_id: str,
    name: str,
    *,
    graph_id: str = "gr_a",
    executor: Executor = Executor.RUNNER,
    judged_by: JudgedBy = JudgedBy.WORKER,
    choices: list[Choice] | None = None,
) -> Node:
    return Node(
        node_id=node_id,
        graph_id=graph_id,
        name=name,
        executor=executor,
        prompt="p",
        checks=[],
        produces=[],
        session=SessionMode.FRESH,
        judged_by=judged_by,
        retries_max=None,
        retries_exhausted=None,
        choices=choices if choices is not None else [],
    )


BUILD = _node(
    "nd_build",
    "build",
    choices=[
        Choice("ch_ship", "ship", "ship it"),
        Choice("ch_review", "review", "", requires_checks=True),
        Choice("ch_hand", "hand-off", ""),
        Choice("ch_gated_hand", "gated-hand-off", "", requires_checks=True),
        Choice("ch_lost", "lost", ""),
    ],
)
_REVIEW = _node("nd_review", "review", choices=[Choice("ch_ok", "ok", "")])
_LAND = _node("nd_land", "land", executor=Executor.HUB, choices=[Choice("ch_landed", "landed", "")])
_GATE = _node(
    "nd_gate", "gate", judged_by=JudgedBy.HUMAN, choices=[Choice("ch_yes", "yes", ""), Choice("ch_no", "no", "")]
)
GRAPH = make_graph(
    "gr_a",
    "work",
    entry_node_id="nd_build",
    nodes=[BUILD, _REVIEW, _LAND, _GATE],
    edges=[
        Edge("nd_build", "ch_ship", RESERVED_TERMINAL),
        Edge("nd_build", "ch_review", "review"),
        Edge("nd_build", "ch_hand", "graph:delivery", target_graph="delivery", model="opus"),
        Edge("nd_build", "ch_gated_hand", "graph:delivery", target_graph="delivery"),
        Edge("nd_build", "ch_lost", "nowhere"),
        Edge("nd_review", "ch_ok", "land"),
        Edge("nd_gate", "ch_yes", "review"),
        Edge("nd_gate", "ch_no", "graph:delivery", target_graph="delivery"),
    ],
)
DELIVERY = make_graph(
    "gr_d",
    "delivery",
    entry_node_id="nd_d_entry",
    nodes=[
        _node("nd_d_entry", "entry", graph_id="gr_d"),
        _node("nd_d_review", "review", graph_id="gr_d"),
        _node("nd_d_build", "build", graph_id="gr_d"),
        _node("nd_d_land", "land", graph_id="gr_d", executor=Executor.HUB),
    ],
)
CHUNK = Chunk(chunk_id="chk_1", graph_id="gr_a", work_refs=[], minted_at=_T0)
AT_BUILD = ChunkFacts(minted=True, promoted=True, leases=[LeaseFact(epoch=1, minted_at=_T0)])


class _SubmissionFields(TypedDict, total=False):
    check_results: Sequence[CheckOutcome]
    artifacts: Sequence[CompletionArtifact]
    decision_id: str | None
    route_token: str | None
    lease_id: str | None


def completion_submission(choice: str, *, from_node_id: str = "nd_build", **fields: Unpack[_SubmissionFields]) -> Completion:
    return Completion(choice=choice, epoch=1, runner_id="runner-a", from_node_id=from_node_id, **fields)


def gate_decision(
    *, chunk_id: str = "chk_1", node_id: str = "nd_gate", resolved: str | None = None, transitioned: bool = False
) -> GateDecision:
    return GateDecision(
        decision_id="dec_1",
        chunk_id=chunk_id,
        node_id=node_id,
        node_name="gate",
        epoch=1,
        choices=[DecisionChoice(name="yes", description=""), DecisionChoice(name="no", description="")],
        submitted_at=_T0,
        resolved_choice=resolved,
        transitioned=transitioned,
    )


# --- Attempt coherence -------------------------------------------------------


def test_a_report_at_the_current_epoch_must_come_from_the_current_node() -> None:
    refuse_incoherent_attempt(AT_BUILD, GRAPH, from_node=BUILD, epoch=1)
    with pytest.raises(CompletionNotAtCurrentNode):
        refuse_incoherent_attempt(AT_BUILD, GRAPH, from_node=_REVIEW, epoch=1)


def test_a_moved_chunk_judges_the_report_against_its_newest_movement() -> None:
    moved = replace(
        AT_BUILD,
        transitions=[
            TransitionFact(
                to_node_id="nd_review", to_node_executor=Executor.RUNNER, epoch=1, recorded_at=T1, graph_id="gr_a"
            )
        ],
    )
    refuse_incoherent_attempt(moved, GRAPH, from_node=_REVIEW, epoch=1)
    with pytest.raises(CompletionNotAtCurrentNode):
        refuse_incoherent_attempt(moved, GRAPH, from_node=BUILD, epoch=1)


def test_a_report_at_another_epoch_is_left_to_the_write_fence() -> None:
    refuse_incoherent_attempt(AT_BUILD, GRAPH, from_node=_REVIEW, epoch=0)


def test_an_attempt_whose_own_escalation_is_open_is_parked() -> None:
    escalated = replace(AT_BUILD, escalations=[EscalationFact(epoch=1, recorded_at=T1)])
    with pytest.raises(ChunkEscalated):
        refuse_incoherent_attempt(escalated, GRAPH, from_node=BUILD, epoch=1)


def test_the_hubs_own_unresolvable_target_escalation_parks_all_but_a_re_escalation() -> None:
    unresolvable = EscalationFact(epoch=1, recorded_at=T1, cause=EscalationCause.MIGRATION_TARGET_UNRESOLVABLE)
    escalated = replace(AT_BUILD, escalations=[unresolvable])
    refuse_incoherent_attempt(escalated, GRAPH, from_node=BUILD, epoch=1, re_escalating=True)
    with pytest.raises(ChunkEscalated):
        refuse_incoherent_attempt(escalated, GRAPH, from_node=BUILD, epoch=1)


def test_the_hubs_own_escalate_choice_escalation_parks_all_but_a_re_escalation() -> None:
    chosen = EscalationFact(epoch=1, recorded_at=T1, cause=EscalationCause.ESCALATE_CHOICE)
    escalated = replace(AT_BUILD, escalations=[chosen])
    refuse_incoherent_attempt(escalated, GRAPH, from_node=BUILD, epoch=1, re_escalating=True)
    with pytest.raises(ChunkEscalated):
        refuse_incoherent_attempt(escalated, GRAPH, from_node=BUILD, epoch=1)


def test_a_runner_escalation_parks_even_a_re_escalation() -> None:
    escalated = replace(AT_BUILD, escalations=[EscalationFact(epoch=1, recorded_at=T1)])
    with pytest.raises(ChunkEscalated):
        refuse_incoherent_attempt(escalated, GRAPH, from_node=BUILD, epoch=1, re_escalating=True)


def test_an_escalation_superseded_by_a_later_lease_parks_nothing() -> None:
    superseded = replace(
        AT_BUILD,
        leases=[LeaseFact(epoch=1, minted_at=_T0), LeaseFact(epoch=1, minted_at=T1 + timedelta(minutes=1))],
        escalations=[EscalationFact(epoch=1, recorded_at=T1)],
    )
    refuse_incoherent_attempt(superseded, GRAPH, from_node=BUILD, epoch=1)


def test_an_attempt_whose_own_question_is_unanswered_is_parked() -> None:
    asked = QuestionFact(question_id="qn_1", asked_at=T1, epoch=1)
    with pytest.raises(OpenQuestion, match="qn_1"):
        refuse_incoherent_attempt(replace(AT_BUILD, questions=[asked]), GRAPH, from_node=BUILD, epoch=1)
    answered = replace(asked, answered=True)
    refuse_incoherent_attempt(replace(AT_BUILD, questions=[answered]), GRAPH, from_node=BUILD, epoch=1)
    earlier = replace(asked, epoch=0)
    refuse_incoherent_attempt(replace(AT_BUILD, questions=[earlier]), GRAPH, from_node=BUILD, epoch=1)


def test_a_runner_report_out_of_a_hub_executed_node_is_refused() -> None:
    refuse_hub_executed(BUILD)
    with pytest.raises(HubExecutedNode):
        refuse_hub_executed(_LAND)


# --- Runner-config gates -----------------------------------------------------


def test_a_runner_config_gate_needs_a_known_node_with_choices() -> None:
    assert gate_refusal(GRAPH, "nd_build") == BUILD
    with pytest.raises(CompletionRefused, match="no node nd_missing"):
        gate_refusal(GRAPH, "nd_missing")
    with pytest.raises(CompletionRefused, match="no choices"):
        gate_refusal(make_graph("gr_b", "b", nodes=[_node("nd_bare", "bare", graph_id="gr_b")]), "nd_bare")


def test_a_gate_offers_the_nodes_own_choices() -> None:
    assert decision_choices(_GATE) == [
        DecisionChoice(name="yes", description=""),
        DecisionChoice(name="no", description=""),
    ]


# --- Plain completion plan ---------------------------------------------------


def _plain(submission: Completion, *, node: Node = BUILD, gate: GateDecision | None = None) -> CompletionPlan:
    return CompletionPlan.plain(GRAPH, node, submission, open_gate=gate, produces_mode="enforce")


def test_a_plain_completion_lands_on_its_edges_destination() -> None:
    assert _plain(completion_submission("ship")).to_node_id == RESERVED_TERMINAL
    plan = _plain(completion_submission("review", check_results=[CheckOutcome(command="make test", passed=True)]))
    assert (plan.to_node_id, plan.migrates) == ("nd_review", False)


def test_a_cross_graph_choice_migrates_instead() -> None:
    plan = _plain(completion_submission("hand-off"))
    assert plan.migrates and plan.edge.target_graph == "delivery"


def test_cross_graph_plain_completion_checks_the_source_nodes_outputs() -> None:
    source = replace(BUILD, produces=[ProducesSpec(name="handoff-notes")])
    with pytest.raises(CompletionRefused, match="handoff-notes"):
        _plain(completion_submission("hand-off"), node=source)
    attached = CompletionArtifact(name="handoff-notes", kind=ArtifactKind.ASSET, content="ready", attached=True)
    assert _plain(completion_submission("hand-off", artifacts=[attached]), node=source).migrates


def test_a_cross_graph_choice_that_requires_green_checks_is_refused_over_a_red_one() -> None:
    red = [CheckOutcome(command="make test", passed=False)]
    with pytest.raises(CompletionRefused, match="requires green checks"):
        _plain(completion_submission("gated-hand-off", check_results=red))
    green = [CheckOutcome(command="make test", passed=True)]
    assert _plain(completion_submission("gated-hand-off", check_results=green)).migrates


def test_a_human_judged_node_is_left_only_by_its_resolving_transition() -> None:
    with pytest.raises(CompletionRefused, match="human signoff required"):
        _plain(completion_submission("yes", from_node_id="nd_gate"), node=_GATE)


def test_an_open_runner_config_gate_refuses_the_plain_completion() -> None:
    with pytest.raises(GateDecisionOpen):
        _plain(completion_submission("ship"), gate=gate_decision(node_id="nd_build"))


def test_a_resolved_but_uncarried_runner_config_gate_refuses_the_plain_completion() -> None:
    gate = gate_decision(node_id="nd_build", resolved="yes")
    with pytest.raises(GateResolutionUnapplied, match="resolved to `yes` — submit its resolving transition"):
        _plain(completion_submission("ship"), gate=gate)
    with pytest.raises(GateResolutionUnapplied):
        _plain(completion_submission("yes"), gate=gate)


def test_a_runner_config_gate_a_transition_carried_no_longer_refuses() -> None:
    gate = replace(gate_decision(node_id="nd_build", resolved="yes"), transitioned=True)
    assert _plain(completion_submission("ship"), gate=gate).to_node_id == RESERVED_TERMINAL


def test_an_unknown_choice_or_destination_is_refused() -> None:
    with pytest.raises(CompletionRefused, match="no choice `nope`"):
        _plain(completion_submission("nope"))
    with pytest.raises(CompletionRefused, match="unknown node nowhere"):
        _plain(completion_submission("lost"))


def test_a_red_check_refuses_a_choice_that_requires_green_ones() -> None:
    red = [CheckOutcome(command="make test", passed=False)]
    with pytest.raises(CompletionRefused, match="requires green checks"):
        _plain(completion_submission("review", check_results=red))


# --- Resolving completion plan -----------------------------------------------


def _resolving(choice: str, decision: GateDecision | None) -> CompletionPlan:
    submission = completion_submission(choice, from_node_id="nd_gate", decision_id="dec_1")
    return CompletionPlan.resolving(GRAPH, _GATE, submission, decision, chunk=CHUNK)


def test_a_resolving_completion_takes_the_resolved_choice() -> None:
    assert _resolving("yes", gate_decision(resolved="yes")).to_node_id == "nd_review"
    assert _resolving("no", gate_decision(resolved="no")).migrates


@pytest.mark.parametrize(
    ("decision", "detail"),
    [
        (None, "does not match node `gate`"),
        (gate_decision(chunk_id="chk_other", resolved="yes"), "does not match node `gate`"),
        (gate_decision(node_id="nd_build", resolved="yes"), "does not match node `gate`"),
        (gate_decision(), "is not yet resolved"),
        (gate_decision(resolved="no"), "is not the resolved choice `no`"),
    ],
)
def test_a_resolving_completion_naming_a_gate_it_cannot_leave_is_refused(
    decision: GateDecision | None, detail: str
) -> None:
    with pytest.raises(CompletionRefused, match=detail):
        _resolving("yes", decision)


# --- Migration replay --------------------------------------------------------


def migration_fact(source: MigrationSource | None, executor: Executor = Executor.RUNNER) -> MigrationFact:
    return MigrationFact(
        from_node_id="nd_build",
        from_graph_id="gr_a",
        to_graph_id="gr_d",
        landed_node_id="nd_d_build",
        choice_name="hand-off",
        model=None,
        epoch=1,
        recorded_at=T1,
        landed_node_executor=executor,
        source=source,
    )


@pytest.mark.parametrize(
    ("migration", "replay"),
    [
        (migration_fact(MigrationSource.RESTART), ReplayedMigration.SUPERSEDED_BY_RESTART),
        (migration_fact(MigrationSource.AUTHORED_EDGE, Executor.HUB), ReplayedMigration.HUB_NODE_TAKEN),
        (migration_fact(MigrationSource.AUTHORED_EDGE), ReplayedMigration.MIGRATED),
    ],
)
def test_a_replayed_migration_is_classified_by_what_landed(migration: MigrationFact, replay: ReplayedMigration) -> None:
    facts = replace(AT_BUILD, migrations=[migration])
    assert replayed_migration(facts, from_node_id="nd_build", epoch=1) is replay


def test_a_replay_answers_without_claiming_a_fresh_migration() -> None:
    superseded = ApplyResult.replayed(ReplayedMigration.SUPERSEDED_BY_RESTART, epoch=3)
    assert superseded.detail == "superseded by a restart at epoch 3"
    for replay in ReplayedMigration:
        assert not ApplyResult.replayed(replay, epoch=1).fresh_migration


# --- Migration targets and the landing consult -------------------------------

_NEWER_WORK = replace(
    make_graph("gr_a2", "work", entry_node_id="nd_build", nodes=[]), created_at=T1 + timedelta(days=1)
)
_ON = FollowLatest(enabled=True)


def _targets(chunk: Chunk, *, intent_retired: bool = False, follow: FollowLatest = _ON) -> MigrationTargets:
    return MigrationTargets.of(
        chunk,
        GRAPH,
        cross_graph=DELIVERY,
        intent_graph=DELIVERY,
        intent_retired=intent_retired,
        follow_latest=follow,
        newest_same_name=_NEWER_WORK,
    )


def _with_intent(node_name: str | None) -> Chunk:
    return replace(CHUNK, intended_migration=IntendedMigration.toward("gr_d", node_name))


def test_follow_latest_drifts_only_without_an_intent() -> None:
    assert _targets(CHUNK).follow_latest == _NEWER_WORK
    assert _targets(CHUNK, follow=FollowLatest(enabled=False)).follow_latest is None
    with_intent = _targets(_with_intent(None))
    assert (with_intent.intended, with_intent.follow_latest) == (DELIVERY, None)


def test_a_retired_intent_target_moves_nothing() -> None:
    assert _targets(_with_intent(None), intent_retired=True).intended is None
    assert _targets(CHUNK).intended is None


def test_the_cross_graph_name_is_the_submitted_choices_target() -> None:
    assert MigrationTargets.cross_graph_name(GRAPH, completion_submission("hand-off")) == "delivery"
    assert MigrationTargets.cross_graph_name(GRAPH, completion_submission("ship")) is None


def _review_edge() -> Edge:
    return Edge("nd_build", "ch_review", "review")


def test_a_forced_intent_lands_on_its_named_node() -> None:
    chunk = _with_intent("land")
    landing = Landing.consult(chunk, _review_edge(), _targets(chunk))
    assert landing is not None
    assert (landing.graph, landing.node_id, landing.source, landing.clear_intent) == (
        DELIVERY,
        "nd_d_land",
        MigrationSource.INTENT,
        True,
    )
    assert not landing.releases_route


def test_an_auto_intent_lands_on_the_same_named_node_or_waits() -> None:
    chunk = _with_intent(None)
    landing = Landing.consult(chunk, _review_edge(), _targets(chunk))
    assert landing is not None and landing.node_id == "nd_d_review" and landing.releases_route
    assert Landing.consult(chunk, Edge("nd_review", "ch_ok", "missing"), _targets(chunk)) is None
    assert Landing.consult(chunk, _review_edge(), _targets(chunk, intent_retired=True)) is None


def test_follow_latest_lands_on_the_same_named_node_except_into_the_terminal() -> None:
    landing = Landing.consult(CHUNK, _review_edge(), _targets(CHUNK))
    assert landing is not None
    assert (landing.graph, landing.source, landing.clear_intent) == (_NEWER_WORK, MigrationSource.FOLLOW_LATEST, False)
    assert Landing.consult(CHUNK, Edge("nd_build", "ch_ship", RESERVED_TERMINAL), _targets(CHUNK)) is None
    assert Landing.consult(CHUNK, _review_edge(), _targets(CHUNK, follow=FollowLatest(enabled=False))) is None


def test_an_authored_edge_lands_on_the_same_named_node_and_repins_its_model() -> None:
    edge = Edge("nd_build", "ch_hand", "graph:delivery", target_graph="delivery", model="opus")
    landing = Landing.authored(edge, DELIVERY, BUILD)
    assert (landing.node_id, landing.source, landing.model) == ("nd_d_build", MigrationSource.AUTHORED_EDGE, "opus")
    assert Landing.authored(edge, DELIVERY, _GATE).node_id == "nd_d_entry"


# --- Next step ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("to_node_id", "kind"),
    [
        (RESERVED_TERMINAL, NextStepKind.DONE),
        ("nd_missing", NextStepKind.MISSING),
        ("nd_land", NextStepKind.HUB_TAKES),
        ("nd_gate", NextStepKind.GATE),
        ("nd_review", NextStepKind.ADVANCE),
    ],
)
def test_the_next_step_follows_the_destination(to_node_id: str, kind: NextStepKind) -> None:
    assert NextStep.of(GRAPH, to_node_id).kind is kind


# --- Stored artifacts --------------------------------------------------------


def test_a_commit_artifact_encodes_branch_and_hash_and_alone_carries_its_repo() -> None:
    commit = CompletionArtifact(
        name="c", kind=ArtifactKind.GIT_COMMIT, forge="github", repo="o/r", branch_name="main", commit_hash="abc"
    )
    note = CompletionArtifact(name="n", kind=ArtifactKind.ASSET, repo="o/r", content="hello")
    rows = stored_artifacts("chk_1", BUILD, 2, [commit, note], artifact_ids=["ar_1", "ar_2"])

    assert [(r.artifact_id, r.data, r.repo, r.forge) for r in rows] == [
        ("ar_1", "main:abc", "o/r", "github"),
        ("ar_2", "hello", None, None),
    ]
    assert {(r.chunk_id, r.node_id, r.node_name, r.epoch) for r in rows} == {("chk_1", "nd_build", "build", 2)}


def test_every_submitted_artifact_gets_exactly_one_minted_id() -> None:
    note = CompletionArtifact(name="n", kind=ArtifactKind.ASSET)
    with pytest.raises(ValueError):
        stored_artifacts("chk_1", BUILD, 1, [note], artifact_ids=[])
