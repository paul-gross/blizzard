"""The flow record's one-transaction writes (component tier). A turn is taken only when its submission is open, free of
an open turn, on flows no other turn holds, and behind no eligible arrival, and several flows are taken together or
not at all. A turn's end commits with the writes that route its answer — a transition, a bounce and escalation, or a
poll row — or not at all, and a fenced exit still ends the turn with ``withdrawal_for``'s closure. Every hub-node
escalation writes the visit's epoch plus one. A landing on a flow writes its five rows once, and a lever withdraws a
waiting submission and leaves a mid-turn one to its turn."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import Table, func, select

from blizzard.hub.domain.chunk.flow_writes import FlowFactWritten
from blizzard.hub.domain.chunk.model import WorkRef, decide_close_enqueue
from blizzard.hub.domain.chunk.ports.escalations import BounceEscalationOutcome
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from blizzard.hub.domain.delivery.flows import (
    FlowLanding,
    FlowResource,
    LandingWrite,
    Submission,
    TurnEvent,
    withdrawal_for,
)
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.flow_store import FlowStore
from tests.support import HubHarness, build_hub, chunk_stores, hub_scoped, hub_store_connections, ingest

pytestmark = pytest.mark.component

_MAIN = FlowResource("re_a", "main")
_OTHER = FlowResource("re_b", "master")
_NODE = "nd_deliver"
_HUB_RUNNER = "hub"


def _flow_id(submission: Submission) -> str:
    (flow,) = submission.flows
    return flow.flow_id


class _Abort(Exception):
    """Raised inside a turn's ending transaction to roll it back."""


@dataclass(frozen=True)
class _World:
    hub: HubHarness
    flows: FlowStore
    chunks: ChunkStores

    def chunk(self, ref: str = "12") -> str:
        return ingest(self.hub, [{"source": "default", "ref": ref}])

    def submit(self, chunk_id: str, *resources: FlowResource) -> Submission:
        submission = self.flows.submit(
            chunk_id, node_id=_NODE, epoch=0, generation=0, resources=list(resources), at=self.hub.clock.now()
        )
        assert submission is not None
        return submission

    def take(self, submission: Submission, *, passed_over: frozenset[str] = frozenset()) -> str | None:
        turn = self.flows.take_turn(
            submission.submission_id, passed_over=passed_over, terms=(), at=self.hub.clock.now()
        )
        return None if turn is None else turn.turn_id

    def count(self, table: Table) -> int:
        with self.hub.engine.connect() as conn:
            return int(conn.execute(hub_scoped(select(func.count()).select_from(table))).scalar() or 0)

    def closed(self, submission: Submission) -> str | None:
        visit = self.flows.submission_for_visit(submission.chunk_id, node_id=submission.node_id, epoch=submission.epoch)
        assert visit is not None
        return visit.closed

    def kinds(self, submission: Submission) -> list[tuple[str, str | None]]:
        return [(f.kind, f.outcome) for f in self.flows.facts_of(submission.submission_id)]

    def stop(self, chunk_id: str) -> None:
        assert self.hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"}).status_code == 202


@pytest.fixture
def world(tmp_path: Path) -> _World:
    hub = build_hub(tmp_path)
    return _World(hub, FlowStore(hub_store_connections(hub.engine), hub.clock), chunk_stores(hub.engine, hub.clock))


# --- taking a turn ----------------------------------------------------------------------------------------------------


def test_a_turn_writes_a_turn_started_fact_per_flow_it_takes(world: _World) -> None:
    submission = world.submit(world.chunk(), _MAIN, _OTHER)

    turn_id = world.take(submission)

    assert turn_id is not None
    assert [t.turn_id for t in world.flows.open_turns()] == [turn_id]
    assert world.kinds(submission) == [("submitted", None)] * 2 + [("turn-started", None)] * 2


def test_a_busy_flow_refuses_a_turn_to_a_submission_it_passed_over(world: _World) -> None:
    first = world.submit(world.chunk("12"), _MAIN)
    second = world.submit(world.chunk("13"), _MAIN)
    assert world.take(first) is not None

    assert world.take(second, passed_over=frozenset({first.submission_id})) is None


def test_a_visit_with_an_open_turn_takes_no_second(world: _World) -> None:
    submission = world.submit(world.chunk(), _MAIN)
    assert world.take(submission) is not None

    assert world.take(submission) is None
    assert len(world.flows.open_turns()) == 1


def test_a_closed_submission_takes_no_turn(world: _World) -> None:
    submission = world.submit(world.chunk())
    world.flows.close(submission.submission_id, outcome="superseded", at=world.hub.clock.now())

    assert world.take(submission) is None


def test_an_earlier_arrival_not_passed_over_keeps_the_turn_from_a_later_one(world: _World) -> None:
    earlier = world.submit(world.chunk("12"), _MAIN)
    later = world.submit(world.chunk("13"), _MAIN)

    assert world.take(later) is None
    assert world.take(later, passed_over=frozenset({earlier.submission_id})) is not None


def test_several_flows_are_taken_together_or_not_at_all(world: _World) -> None:
    holding_other = world.submit(world.chunk("12"), _OTHER)
    both = world.submit(world.chunk("13"), _MAIN, _OTHER)
    assert world.take(holding_other) is not None

    assert world.take(both) is None
    assert world.count(s.hub_turns) == 1
    assert [k for k, _ in world.kinds(both)] == ["submitted", "submitted"]
    alone_on_main = world.submit(world.chunk("14"), _MAIN)
    assert world.take(alone_on_main, passed_over=frozenset({both.submission_id})) is not None


def test_a_visit_holding_nothing_takes_a_turn_whenever_it_has_none_open(world: _World) -> None:
    first = world.submit(world.chunk("12"))
    second = world.submit(world.chunk("13"))

    assert world.take(first) is not None
    assert world.take(second) is not None


# --- ending a turn ----------------------------------------------------------------------------------------------------


def _transition(world: _World, handle: object, chunk_id: str, *, epoch: int = 0) -> bool:
    return world.chunks.hub_exec.record_hub_step_transition_locked(
        handle,  # type: ignore[arg-type]
        chunk_id,
        from_node_id=_NODE,
        to_node_id="nd_done",
        choice_name="landed",
        epoch=epoch,
        admission=EpochAdmission.AT_OR_ABOVE,
        runner_id=_HUB_RUNNER,
        transition_id=f"tr_{chunk_id}",
        close_decision=decide_close_enqueue([WorkRef("default", "12")], lambda _: True),
        at=world.hub.clock.now(),
        artifacts=[],
        release_route=False,
    )


def test_a_verdict_commits_its_transition_with_the_turns_end_and_closure(world: _World) -> None:
    submission = world.submit(world.chunk(), _MAIN)
    turn_id = world.take(submission)
    assert turn_id is not None

    with world.chunks.exclusive.locked([submission.chunk_id]) as handle:
        assert _transition(world, handle, submission.chunk_id)
        assert world.flows.end_turn_locked(
            handle, turn_id, outcome="landed", closure="landed", event=None, at=world.hub.clock.now()
        ) == (
            FlowFactWritten(
                _flow_id(submission), "turn-ended", submission.submission_id, submission.chunk_id, "landed"
            ),
            FlowFactWritten(_flow_id(submission), "withdrawn", submission.submission_id, submission.chunk_id, "landed"),
        )

    assert world.count(s.transitions) == 1
    assert world.flows.open_turns() == []
    assert world.closed(submission) == "landed"
    assert world.kinds(submission)[-2:] == [("turn-ended", "landed"), ("withdrawn", "landed")]


def test_an_ending_that_fails_writes_neither_its_transition_nor_the_turns_end(world: _World) -> None:
    submission = world.submit(world.chunk(), _MAIN)
    turn_id = world.take(submission)
    assert turn_id is not None

    with pytest.raises(_Abort), world.chunks.exclusive.locked([submission.chunk_id]) as handle:
        _transition(world, handle, submission.chunk_id)
        world.flows.end_turn_locked(
            handle, turn_id, outcome="landed", closure="landed", event=None, at=world.hub.clock.now()
        )
        raise _Abort

    assert world.count(s.transitions) == 0
    assert [t.turn_id for t in world.flows.open_turns()] == [turn_id]
    assert world.closed(submission) is None


def test_an_escalation_commits_its_bounce_and_hub_lease_with_the_turns_end_or_not_at_all(world: _World) -> None:
    submission = world.submit(world.chunk(), _MAIN)
    turn_id = world.take(submission)
    assert turn_id is not None

    def escalate(*, fail: bool) -> None:
        with world.chunks.exclusive.locked([submission.chunk_id]) as handle:
            world.chunks.escalations.record_bounce_locked(
                handle,
                submission.chunk_id,
                epoch=1,
                admission=EpochAdmission.ABOVE,
                cause="poll-timeout",
                envelope="{}",
                at=world.hub.clock.now(),
            )
            world.chunks.escalations.record_bounce_escalation_locked(
                handle,
                submission.chunk_id,
                epoch=1,
                admission=EpochAdmission.ABOVE,
                runner_id=_HUB_RUNNER,
                takeover_command="",
                at=world.hub.clock.now(),
                cause="bounce-cap",
                detail="crossed",
            )
            world.flows.end_turn_locked(
                handle, turn_id, outcome="escalated", closure=None, event=None, at=world.hub.clock.now()
            )
            if fail:
                raise _Abort

    with pytest.raises(_Abort):
        escalate(fail=True)
    assert (world.count(s.chunk_bounces), world.count(s.escalations), len(world.flows.open_turns())) == (0, 0, 1)

    escalate(fail=False)

    assert (world.count(s.chunk_bounces), world.count(s.escalations), world.flows.open_turns()) == (1, 1, [])
    assert world.closed(submission) is None, "an escalated visit keeps its place"


def test_a_wait_commits_its_poll_row_with_the_turns_end(world: _World) -> None:
    submission = world.submit(world.chunk(), _MAIN)
    turn_id = world.take(submission)
    assert turn_id is not None

    with world.chunks.exclusive.locked([submission.chunk_id]) as handle:
        world.chunks.hub_exec.record_hub_node_poll_locked(
            handle, submission.chunk_id, node_id=_NODE, epoch=0, kind="pending", at=world.hub.clock.now()
        )
        world.flows.end_turn_locked(
            handle, turn_id, outcome="pending", closure=None, event=None, at=world.hub.clock.now()
        )

    assert world.count(s.hub_node_poll) == 1
    assert world.flows.turn_outcomes(submission.chunk_id, node_id=_NODE, epoch=0) == ["pending"]
    assert world.closed(submission) is None


def test_consecutive_parked_polls_keep_first_instant_and_advance_latest(world: _World) -> None:
    chunk = world.chunk()
    first = world.hub.clock.now()
    with world.chunks.exclusive.locked([chunk]) as handle:
        for at in (first, first + timedelta(minutes=2), first + timedelta(minutes=4)):
            world.chunks.hub_exec.record_hub_node_poll_locked(
                handle, chunk, node_id=_NODE, epoch=0, kind="awaiting-merge", at=at
            )

    with world.chunks.exclusive.locked([chunk]) as handle:
        facts = handle.facts(chunk)
        assert facts is not None
        [poll] = facts.hub_node_poll_history(node_id=_NODE, epoch=0)
        assert (poll.polled_at, poll.last_polled_at) == (first, first + timedelta(minutes=4))


def test_a_fenced_exit_still_ends_the_turn_with_the_closure_withdrawal_for_answers(world: _World) -> None:
    submission = world.submit(world.chunk(), _MAIN)
    turn_id = world.take(submission)
    assert turn_id is not None
    world.stop(submission.chunk_id)
    assert world.closed(submission) is None, "a lever leaves a mid-turn submission to its turn's end"

    with world.chunks.exclusive.locked([submission.chunk_id]) as handle:
        assert not _transition(world, handle, submission.chunk_id)
        facts = handle.facts(submission.chunk_id)
        assert facts is not None
        assert world.flows.end_turn_locked(
            handle,
            turn_id,
            outcome="landed",
            closure=withdrawal_for(facts, submission),
            event=None,
            at=world.hub.clock.now(),
        )

    assert world.count(s.transitions) == 0
    assert world.closed(submission) == "stopped"
    assert world.kinds(submission)[-2:] == [("turn-ended", "landed"), ("withdrawn", "stopped")]


def test_ending_a_turn_twice_writes_nothing_the_second_time(world: _World) -> None:
    submission = world.submit(world.chunk(), _MAIN)
    turn_id = world.take(submission)
    assert turn_id is not None
    overran = TurnEvent(
        kind="hub-action-overran", chunk_id=submission.chunk_id, node_name="deliver", message="", detail={}
    )

    ends = []
    for _ in range(2):
        with world.chunks.exclusive.locked([submission.chunk_id]) as handle:
            ends.append(
                world.flows.end_turn_locked(
                    handle, turn_id, outcome="cut-off", closure=None, event=overran, at=world.hub.clock.now()
                )
            )

    assert [end is not None for end in ends] == [True, False]
    assert world.count(s.event_log) == 1
    assert world.kinds(submission).count(("turn-ended", "cut-off")) == 1


def test_a_superseded_turn_closes_its_submission_in_the_same_write(world: _World) -> None:
    submission = world.submit(world.chunk(), _MAIN)
    turn_id = world.take(submission)
    assert turn_id is not None

    with world.chunks.exclusive.locked([submission.chunk_id]) as handle:
        world.flows.end_turn_locked(
            handle, turn_id, outcome="superseded", closure="superseded", event=None, at=world.hub.clock.now()
        )

    assert world.closed(submission) == "superseded"
    assert world.flows.turn_outcomes(submission.chunk_id, node_id=_NODE, epoch=0) == ["superseded"]


# --- escalations open the next visit ----------------------------------------------------------------------------------


def test_an_escalation_at_the_visits_epoch_plus_one_writes_and_at_its_own_is_refused(world: _World) -> None:
    chunk_id = world.chunk()

    def escalate(epoch: int) -> BounceEscalationOutcome:
        with world.chunks.exclusive.locked([chunk_id]) as handle:
            return world.chunks.escalations.record_bounce_escalation_locked(
                handle,
                chunk_id,
                epoch=epoch,
                admission=EpochAdmission.ABOVE,
                runner_id=_HUB_RUNNER,
                takeover_command="",
                at=world.hub.clock.now(),
                cause="hub-action-stuck",
                detail="cut off three times",
            )

    with world.chunks.exclusive.locked([chunk_id]) as handle:
        assert _transition(world, handle, chunk_id, epoch=1), "the hub's lease puts the visit at epoch 1"

    assert escalate(1) is BounceEscalationOutcome.REFUSED
    assert escalate(2) is BounceEscalationOutcome.RECORDED
    facts = world.chunks.facts.load_facts(chunk_id)
    assert facts is not None and facts.latest_epoch() == 2


def test_the_locked_bounce_is_fenced(world: _World) -> None:
    chunk_id = world.chunk()
    world.stop(chunk_id)

    with world.chunks.exclusive.locked([chunk_id]) as handle:
        wrote = world.chunks.escalations.record_bounce_locked(
            handle,
            chunk_id,
            epoch=1,
            admission=EpochAdmission.ABOVE,
            cause="poll-timeout",
            envelope="{}",
            at=world.hub.clock.now(),
        )

    assert wrote is False
    assert world.count(s.chunk_bounces) == 0


# --- landing ----------------------------------------------------------------------------------------------------------


def test_a_landing_on_a_flow_writes_its_five_rows_once(world: _World) -> None:
    chunk_id = world.chunk()
    submission = world.submit(chunk_id, _MAIN)
    landing = FlowLanding.of(
        submission.flows[0], submission, project_id="prj_default", node_name="deliver", revision="m1"
    )

    def record() -> LandingWrite:
        return world.flows.record_landing(
            landing,
            close_decision=decide_close_enqueue([WorkRef("default", "12")], lambda _: True),
            node_id=_NODE,
            node_name="deliver",
            epoch=0,
            admission=EpochAdmission.AT_OR_ABOVE,
            repo="acme/widget",
            revision="m1",
            at=world.hub.clock.now(),
        )

    counts = (s.artifacts, s.delivery_repo_landed, s.close_intents, s.flow_facts, s.event_log)
    before = [world.count(t) for t in counts]
    first = record()
    after_once = [world.count(t) for t in counts]
    replay = record()

    assert first.written == (FlowFactWritten(_flow_id(submission), "landed", submission.submission_id, chunk_id),), (
        "a landing reports its one landed fact"
    )
    assert replay.written == ()

    assert [a - b for a, b in zip(after_once, before, strict=True)] == [1, 1, 1, 1, 1]
    assert [world.count(t) for t in counts] == after_once
    landed = [f for f in world.flows.facts_of(submission.submission_id) if f.kind == "landed"]
    assert [(f.flow_id, f.revision) for f in landed] == [(submission.flows[0].flow_id, "m1")]
    with world.hub.engine.connect() as conn:
        kind, detail = conn.execute(hub_scoped(select(s.event_log.c.kind, s.event_log.c.detail))).one()
    assert kind == "flow-landed"
    assert json.loads(detail)["members"] == [{"chunk_id": chunk_id, "project_id": "prj_default"}]


# --- levers -----------------------------------------------------------------------------------------------------------


def test_a_restart_withdraws_a_waiting_submission_and_leaves_a_mid_turn_one_to_its_turn(world: _World) -> None:
    waiting = world.submit(world.chunk("12"), _MAIN)
    turning = world.submit(world.chunk("13"), _OTHER)
    assert world.take(turning) is not None

    reported = []
    for submission in (waiting, turning):
        with world.chunks.exclusive.locked([submission.chunk_id]) as handle:
            written = world.chunks.movement.record_restart_locked(
                handle,
                submission.chunk_id,
                from_node_id=_NODE,
                to_node_id=_NODE,
                by="operator",
                at=world.hub.clock.now(),
            )
        reported.append(written.withdrawn)

    assert reported == [
        (FlowFactWritten(_flow_id(waiting), "withdrawn", waiting.submission_id, waiting.chunk_id, "restarted"),),
        (),
    ]
    assert world.closed(waiting) == "restarted"
    assert world.closed(turning) is None


def test_a_visits_standing_counts_the_open_arrivals_ahead_of_it(world: _World) -> None:
    first = world.submit(world.chunk("12"), _MAIN)
    second = world.submit(world.chunk("13"), _MAIN, _OTHER)
    assert world.take(first) is not None

    standing = world.flows.visit_standing(second.chunk_id, node_id=_NODE, epoch=0)
    first_standing = world.flows.visit_standing(first.chunk_id, node_id=_NODE, epoch=0)

    assert standing is not None and standing.turning is False
    assert dict(zip((f.resource for f in second.flows), standing.ahead, strict=True)) == {
        _MAIN.resource: 1,
        _OTHER.resource: 0,
    }
    assert first_standing is not None and first_standing.turning is True
