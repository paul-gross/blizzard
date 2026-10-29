"""The line's rules (unit tier): who is eligible and who is next on a flow, what a visit holds, the one judge of a
closure's outcome, the terms a visit's repositories would take, and ``hub:flow-line-standing`` over planted flows
and facts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.node_steps import Executor
from blizzard.hub.domain.chunk.model import (
    ChunkFacts,
    EscalationFact,
    HubNodePollFact,
    LeaseFact,
    PauseFact,
    RestartFact,
    TransitionFact,
)
from blizzard.hub.domain.config.forges import ForgeId
from blizzard.hub.domain.config.landing_policies import LandingPolicy
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    RepositoryFields,
    RepositoryId,
    RepositoryOutcome,
    RepositoryResolution,
    ResolvedRepository,
)
from blizzard.hub.domain.delivery.flows import (
    RECONCILE_FLOOR,
    Flow,
    FlowFact,
    FlowLine,
    FlowResource,
    LandingTerms,
    PollPolicy,
    Submission,
    Turn,
    held_resources,
    stamp_terms,
    withdrawal_for,
)
from blizzard.hub.domain.graph.actions import ActionSpec
from blizzard.hub.store.internal.invariants import FlowLineStanding

pytestmark = pytest.mark.unit

_AT = datetime(2026, 10, 27, 12, 0, 0, tzinfo=UTC)
_DELIVER = "nd_deliver"
_MAIN = Flow("flw_main", "repo_a", "main", _AT)
_OTHER = Flow("flw_other", "repo_b", "master", _AT)


def _at_node(node_id: str = _DELIVER, *, epoch: int = 1, at: datetime = _AT, **overrides: object) -> ChunkFacts:
    """A promoted chunk whose newest movement entered hub node ``node_id`` at ``epoch``."""
    facts = ChunkFacts(
        minted=True,
        promoted=True,
        leases=[LeaseFact(epoch=epoch, minted_at=at)],
        transitions=[TransitionFact(node_id, Executor.HUB, epoch, at, from_node_id="nd_build", choice_name="pass")],
    )
    return replace(facts, **overrides)  # type: ignore[arg-type]


def _submission(
    chunk_id: str, arrival: int, *flows: Flow, node_id: str = _DELIVER, epoch: int = 1, submission_id: str = ""
) -> Submission:
    return Submission(
        submission_id=submission_id or f"fsub_{chunk_id}",
        chunk_id=chunk_id,
        node_id=node_id,
        epoch=epoch,
        generation=0,
        arrival=arrival,
        submitted_at=_AT,
        flows=flows,
    )


def _line(*entries: tuple[Submission, ChunkFacts], turning: frozenset[str] = frozenset()) -> FlowLine:
    return FlowLine(
        submissions=tuple(sub for sub, _ in entries),
        facts={sub.chunk_id: facts for sub, facts in entries},
        policies={sub.submission_id: PollPolicy() for sub, _ in entries},
        turning=turning,
    )


# --- Eligibility -----------------------------------------------------------------------------------------------------


def test_a_delivering_current_visit_with_no_turn_and_no_poll_is_eligible() -> None:
    sub = _submission("ch_1", 1, _MAIN)

    assert _line((sub, _at_node())).eligible(sub, now=_AT)


def test_a_visit_whose_chunk_is_not_delivering_is_not_eligible() -> None:
    sub = _submission("ch_1", 1, _MAIN)
    paused = _at_node(pauses=[PauseFact(paused=True, set_at=_AT, set_by="op")])

    assert not _line((sub, paused)).eligible(sub, now=_AT)


def test_a_visit_its_chunk_has_left_is_not_eligible() -> None:
    moved = _submission("ch_1", 1, _MAIN, node_id="nd_review")
    later = _submission("ch_2", 2, _MAIN, epoch=1)

    assert not _line((moved, _at_node())).eligible(moved, now=_AT)
    assert not _line((later, _at_node(epoch=2))).eligible(later, now=_AT)


def test_a_visit_with_an_open_turn_is_not_eligible() -> None:
    sub = _submission("ch_1", 1, _MAIN)

    assert not _line((sub, _at_node()), turning=frozenset({sub.submission_id})).eligible(sub, now=_AT)


def test_a_waiting_visit_is_eligible_only_once_its_poll_is_due() -> None:
    sub = _submission("ch_1", 1, _MAIN)
    polled = _at_node(hub_node_polls=[HubNodePollFact(_DELIVER, 1, _AT)])
    line = _line((sub, polled))
    due = PollPolicy().due_at(HubNodePollFact(_DELIVER, 1, _AT))

    assert not line.eligible(sub, now=due - timedelta(seconds=1))
    assert line.eligible(sub, now=due)


def test_a_poll_rows_next_poll_at_postpones_eligibility() -> None:
    sub = _submission("ch_1", 1, _MAIN)
    later = _AT + timedelta(minutes=10)
    line = _line((sub, _at_node(hub_node_polls=[HubNodePollFact(_DELIVER, 1, _AT, next_poll_at=later)])))

    assert not line.eligible(sub, now=_AT + timedelta(minutes=5))
    assert line.eligible(sub, now=later)


# --- Who is next -----------------------------------------------------------------------------------------------------


def test_the_oldest_eligible_arrival_is_next_on_its_flow() -> None:
    first, second = _submission("ch_1", 1, _MAIN), _submission("ch_2", 2, _MAIN)
    line = _line((first, _at_node()), (second, _at_node()))

    assert line.is_next(first, now=_AT)
    assert not line.is_next(second, now=_AT)


def test_an_ineligible_earlier_arrival_blocks_nothing() -> None:
    waiting, second = _submission("ch_1", 1, _MAIN), _submission("ch_2", 2, _MAIN)
    polled = _at_node(hub_node_polls=[HubNodePollFact(_DELIVER, 1, _AT)])
    line = _line((waiting, polled), (second, _at_node()))

    assert line.is_next(second, now=_AT)


def test_an_earlier_arrival_on_another_flow_blocks_nothing() -> None:
    first, second = _submission("ch_1", 1, _OTHER), _submission("ch_2", 2, _MAIN)

    assert _line((first, _at_node()), (second, _at_node())).is_next(second, now=_AT)


def test_a_visit_on_two_flows_is_next_only_when_next_on_both() -> None:
    ahead, both = _submission("ch_1", 1, _OTHER), _submission("ch_2", 2, _MAIN, _OTHER)
    line = _line((ahead, _at_node()), (both, _at_node()))

    assert not line.is_next(both, now=_AT)


def test_a_visit_holding_nothing_is_next_whenever_it_is_eligible() -> None:
    ahead, nothing = _submission("ch_1", 1, _MAIN), _submission("ch_2", 2)
    line = _line((ahead, _at_node()), (nothing, _at_node()))

    assert line.is_next(nothing, now=_AT)
    assert not _line((nothing, _at_node(epoch=2))).is_next(nothing, now=_AT)


# --- Places --------------------------------------------------------------------------------------------------------


def _places(line: FlowLine, sub: Submission, *, now: datetime = _AT) -> list[tuple[str, str, int]]:
    return [(place.flow.flow_id, place.state, place.position) for place in line.places_of(sub, now=now)]


def test_the_turning_submission_is_landing_and_the_next_eligible_one_is_next() -> None:
    first, second, third = (_submission(f"ch_{i}", i, _MAIN) for i in (1, 2, 3))
    line = _line((first, _at_node()), (second, _at_node()), (third, _at_node()), turning=frozenset({"fsub_ch_1"}))

    assert _places(line, first) == [("flw_main", "landing", 1)]
    assert _places(line, second) == [("flw_main", "next", 1)]
    assert _places(line, third) == [("flw_main", "waiting", 2)]


def test_a_submission_next_on_one_flow_but_not_another_is_waiting_on_both() -> None:
    ahead, both = _submission("ch_1", 1, _OTHER), _submission("ch_2", 2, _MAIN, _OTHER)
    line = _line((ahead, _at_node()), (both, _at_node()))

    assert _places(line, ahead) == [("flw_other", "next", 1)]
    assert _places(line, both) == [("flw_main", "waiting", 1), ("flw_other", "waiting", 2)]


def test_a_chunk_a_person_holds_is_passed_over_and_counts_for_no_one_behind_it() -> None:
    held, paused, behind = (_submission(f"ch_{i}", i, _MAIN) for i in (1, 2, 3))
    escalated = _at_node(leases=[LeaseFact(2, _AT)], escalations=[EscalationFact(epoch=2, recorded_at=_AT)])
    pause = _at_node(pauses=[PauseFact(paused=True, set_at=_AT, set_by="op")])
    line = _line((held, escalated), (paused, pause), (behind, _at_node()))

    assert _places(line, held) == [("flw_main", "passed-over", 1)]
    assert _places(line, paused) == [("flw_main", "passed-over", 1)]
    assert _places(line, behind) == [("flw_main", "next", 1)]


def test_a_visit_waiting_on_its_poll_is_polling_until_due_with_its_due_instant() -> None:
    first, polled = _submission("ch_1", 1, _MAIN), _submission("ch_2", 2, _MAIN)
    poll = HubNodePollFact(_DELIVER, 1, _AT)
    due = PollPolicy().due_at(poll)
    line = _line((first, _at_node()), (polled, _at_node(hub_node_polls=[poll])))

    [place] = line.places_of(polled, now=_AT)
    assert (place.state, place.position, place.due_at) == ("polling", 2, due)
    assert _places(line, polled, now=due) == [("flw_main", "waiting", 2)]


def test_the_lines_places_run_in_arrival_order() -> None:
    first, second = _submission("ch_1", 1, _MAIN, _OTHER), _submission("ch_2", 2, _MAIN)
    line = _line((first, _at_node()), (second, _at_node()))

    assert [(p.submission.chunk_id, p.flow.flow_id, p.state) for p in line.places(now=_AT)] == [
        ("ch_1", "flw_main", "next"),
        ("ch_1", "flw_other", "next"),
        ("ch_2", "flw_main", "waiting"),
    ]


# --- What a visit holds ----------------------------------------------------------------------------------------------


def _spec(*, lands: bool) -> ActionSpec:
    return ActionSpec(
        name="land",
        args=frozenset(),
        required_args=frozenset(),
        answers=frozenset({"landed"}),
        answers_from_arg=None,
        lands=lands,
    )


def _resolution() -> RepositoryResolution:
    return RepositoryResolution(
        RepositoryOutcome.RESOLVED,
        targets=(
            ResolvedRepository(ForgeId("frg_1"), "acme", "main", "s", record="repo_a"),
            ResolvedRepository(ForgeId("frg_1"), "acme", "trunk", "s", record="repo_b"),
        ),
        qualified=("acme/app", "acme/lib"),
        records=("repo_a", "repo_b"),
    )


def test_a_landing_visit_holds_each_repository_on_its_own_base_branch() -> None:
    assert held_resources(_resolution(), frozenset(), _spec(lands=True)) == (
        FlowResource("repo_a", "main"),
        FlowResource("repo_b", "trunk"),
    )


def test_a_repository_already_landed_is_held_no_longer() -> None:
    assert held_resources(_resolution(), frozenset({"acme/app"}), _spec(lands=True)) == (
        FlowResource("repo_b", "trunk"),
    )


def test_an_action_that_does_not_land_holds_nothing() -> None:
    assert held_resources(_resolution(), frozenset(), _spec(lands=False)) == ()


def test_a_chunk_with_nothing_to_resolve_holds_nothing() -> None:
    nothing = RepositoryResolution(RepositoryOutcome.NOTHING_TO_RESOLVE)

    assert held_resources(nothing, frozenset(), _spec(lands=True)) == ()


# --- The terms a visit's repositories would take ---------------------------------------------------------------------


def test_a_candidate_takes_its_records_policy_and_timeout_and_its_projects_position() -> None:
    record = ConfiguredRepository(
        repository_id=RepositoryId("repo_a"),
        slug="app",
        name="app",
        fields=RepositoryFields(ForgeId("frg_1"), "acme", "app", "main", "s", LandingPolicy.MERGE_ON_CHECKS, None),
        revision=1,
        created_at=_AT,
        created_by="op",
    )

    assert LandingTerms.candidate("repo_a", record, position=2.0) == LandingTerms(
        "repo_a", "merge-on-checks", None, 2.0
    )


def test_a_candidate_with_no_record_takes_the_default_policy_unranked() -> None:
    assert LandingTerms.candidate("repo_x", None, position=None) == LandingTerms(
        "repo_x", "merge-on-checks", None, None
    )


def test_only_a_target_with_terms_is_stamped_from_them() -> None:
    resolution = _resolution()

    stamped = stamp_terms(resolution, {"repo_a": LandingTerms("repo_a", "merge-on-checks", None, 1.0)})

    assert [t.landing_policy for t in stamped.targets] == [LandingPolicy.MERGE_ON_CHECKS] * 2
    assert stamped.targets[1] == resolution.targets[1]
    assert (stamped.qualified, stamped.records) == (resolution.qualified, resolution.records)


def test_a_target_takes_its_position_from_its_terms_and_one_without_terms_from_the_project_now() -> None:
    resolution = _resolution()
    terms = {"repo_a": LandingTerms("repo_a", "merge-on-checks", None, 1.0)}

    stamped = stamp_terms(resolution, terms, {"repo_a": 9.0, "repo_b": 2.0})

    assert [t.landing_position for t in stamped.targets] == [1.0, 2.0]


# --- withdrawal_for --------------------------------------------------------------------------------------------------

_LATER = _AT + timedelta(minutes=5)


def test_a_current_visit_is_not_withdrawn() -> None:
    assert withdrawal_for(_at_node(), _submission("ch_1", 1, _MAIN)) is None


def test_a_stopped_chunk_withdraws_stopped() -> None:
    stopped = _at_node(stopped=True, stopped_at=_LATER)

    assert withdrawal_for(stopped, _submission("ch_1", 1, _MAIN)) == "stopped"


def test_an_operator_completed_chunk_withdraws_completed() -> None:
    completed = _at_node(operator_completed=True, operator_completed_at=_LATER)

    assert withdrawal_for(completed, _submission("ch_1", 1, _MAIN)) == "completed"


def test_a_chunk_restarted_past_the_visit_withdraws_restarted() -> None:
    restart = RestartFact(_DELIVER, _DELIVER, "gr_1", 2, _LATER, to_node_executor=Executor.HUB)

    assert withdrawal_for(_at_node(restarts=[restart]), _submission("ch_1", 1, _MAIN)) == "restarted"


def test_a_chunk_that_moved_on_withdraws_superseded() -> None:
    onward = TransitionFact("nd_review", Executor.HUB, 2, _LATER, from_node_id=_DELIVER, choice_name="landed")
    moved = _at_node()
    moved = replace(moved, transitions=[*moved.transitions, onward], leases=[LeaseFact(2, _LATER)])

    assert withdrawal_for(moved, _submission("ch_1", 1, _MAIN)) == "superseded"


def test_a_chunk_delivering_at_the_node_in_a_later_epoch_withdraws_superseded() -> None:
    assert withdrawal_for(_at_node(leases=[LeaseFact(2, _LATER)]), _submission("ch_1", 1, _MAIN)) == "superseded"


def test_a_person_holding_the_chunk_at_a_later_epoch_keeps_its_place() -> None:
    escalated = _at_node(leases=[LeaseFact(2, _LATER)], escalations=[EscalationFact(epoch=2, recorded_at=_LATER)])
    paused = _at_node(leases=[LeaseFact(2, _LATER)], pauses=[PauseFact(paused=True, set_at=_LATER, set_by="op")])

    assert withdrawal_for(escalated, _submission("ch_1", 1, _MAIN)) is None
    assert withdrawal_for(paused, _submission("ch_1", 1, _MAIN)) is None


def test_a_person_holding_the_chunk_after_it_left_the_node_does_not_keep_its_place() -> None:
    onward = TransitionFact("nd_build", Executor.RUNNER, 2, _LATER, from_node_id=_DELIVER, choice_name="conflict")
    left = _at_node()
    left = replace(
        left,
        transitions=[*left.transitions, onward],
        leases=[LeaseFact(2, _LATER)],
        escalations=[EscalationFact(epoch=2, recorded_at=_LATER)],
    )

    assert withdrawal_for(left, _submission("ch_1", 1, _MAIN)) == "superseded"


# --- hub:flow-line-standing ------------------------------------------------------------------------------------------


@dataclass
class _Flows:
    submissions: list[Submission]
    turns: list[Turn] = field(default_factory=list)

    def open_submissions_from(self, floor: int) -> list[Submission]:
        return [sub for sub in self.submissions if sub.arrival >= floor]

    def open_turns(self) -> list[Turn]:
        return self.turns

    def submission_for_visit(self, chunk_id: str, *, node_id: str, epoch: int) -> Submission | None:
        raise NotImplementedError

    def facts_of(self, submission_id: str) -> list[FlowFact]:
        raise NotImplementedError


@dataclass
class _Facts:
    by_id: dict[str, ChunkFacts]

    def load_facts_for(self, chunk_ids: Sequence[str]) -> dict[str, ChunkFacts]:
        return {chunk_id: self.by_id[chunk_id] for chunk_id in chunk_ids if chunk_id in self.by_id}


_SETTLED = _AT + RECONCILE_FLOOR + timedelta(seconds=1)


def _standing(flows: _Flows, facts: dict[str, ChunkFacts], *, now: datetime = _SETTLED) -> list[str]:
    check = FlowLineStanding(flows, _Facts(facts), FixedClock(now))  # type: ignore[arg-type]
    return [found.invariant for found in check.run()]


def test_flow_line_standing_holds_for_a_current_visit() -> None:
    assert _standing(_Flows([_submission("ch_1", 1, _MAIN)]), {"ch_1": _at_node()}) == []


def test_flow_line_standing_holds_for_a_person_holding_the_chunk_at_a_later_epoch() -> None:
    held = _at_node(leases=[LeaseFact(2, _AT)], escalations=[EscalationFact(epoch=2, recorded_at=_AT)])

    assert _standing(_Flows([_submission("ch_1", 1, _MAIN)]), {"ch_1": held}) == []


def test_flow_line_standing_fails_on_an_open_submission_whose_chunk_left() -> None:
    left = _submission("ch_1", 1, _MAIN, node_id="nd_review")

    assert _standing(_Flows([left]), {"ch_1": _at_node()}) == ["hub:flow-line-standing"]


def test_flow_line_standing_exempts_a_submission_with_an_open_turn() -> None:
    left = _submission("ch_1", 1, _MAIN, node_id="nd_review")
    flows = _Flows([left], turns=[Turn("htn_1", left.submission_id, _AT)])

    assert _standing(flows, {"ch_1": _at_node()}) == []


def test_flow_line_standing_exempts_a_chunk_that_moved_within_the_reconcile_floor() -> None:
    left = _submission("ch_1", 1, _MAIN, node_id="nd_review")
    within = _AT + RECONCILE_FLOOR - timedelta(seconds=1)

    assert _standing(_Flows([left]), {"ch_1": _at_node()}, now=within) == []
    assert _standing(_Flows([left]), {"ch_1": _at_node()}, now=_SETTLED) == ["hub:flow-line-standing"]
