"""A turn's task (component tier), driven through real ticks and real ``ActionLoop`` passes on a hub whose pass bound
is short: a pass that never returns to an ``await`` is cut off and routes nothing, three in a row go to a person, a
build that raises or hangs ends its turn, an end that fails to write is swept, a lever mid-turn waits for the turn's
end, and ground moved under a submission supersedes it. A visit lands by the terms its first turn took, keeps the
moment that turn started, and a pass that waits records a ``pending`` wait."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import insert, select

from blizzard.foundation.clock import FixedClock
from blizzard.hub.delivery.action_registry import HubActions
from blizzard.hub.delivery.flow_turn import TurnRunner
from blizzard.hub.domain.actions.effects import IEffectSink, Marker, Pass
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.domain.chunk.flow_writes import FlowFactWritten
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.repositories import RepositoryEdit
from blizzard.hub.domain.delivery.flows import Submission
from blizzard.hub.domain.graph.actions import ActionSpec
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.forge.budget import BudgetKey, ForgeBudgets, ForgePriority
from blizzard.hub.forge.landing import VisitForges
from blizzard.hub.forge.registry import ForgeCallRefused, TurnCutOff, _BudgetGate, _RefsHandle
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.flow_store import FlowStore
from blizzard.tools.invariants import hub_invariants
from tests.support import (
    OP,
    TEST_HUB_ACTIONS,
    HubHarness,
    build_hub,
    default_project,
    hub_scoped,
    pointer_token,
    report_lease,
    stamp,
)

pytestmark = pytest.mark.component

#: The pass bound and grace every hub here takes: a pass is let go 0.6s after it is handed over.
_BOUND = 0.3
_GRACE = timedelta(seconds=0.3)
_REPO = "acme/widget"


class _Hooked:
    """A landing action calling ``hook`` mid-pass, then answering ``answer``."""

    spec = ActionSpec(
        name="test.hooked",
        args=frozenset(),
        required_args=frozenset(),
        answers=frozenset({"pending", "landed", "failure"}),
        answers_from_arg=None,
        lands=True,
    )

    def __init__(self) -> None:
        self.answer = Pass(choice="pending")
        self.hook: Callable[[Visit], None] = lambda visit: None
        self.passes = 0

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        self.passes += 1
        self.hook(visit)
        return self.answer


class _BlockThenMark:
    """Holds the loop's thread past any deadline, then hands over a marker."""

    spec = ActionSpec(
        name="test.block-then-mark",
        args=frozenset(),
        required_args=frozenset(),
        answers=frozenset({"success"}),
        answers_from_arg=None,
        lands=False,
    )

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        import time

        time.sleep(1.0)
        await effects.record(Marker("late", "written after the deadline"))
        return Pass(choice="success")


class _Mark:
    """Hands over one marker, then answers ``success``."""

    spec = ActionSpec(
        name="test.mark",
        args=frozenset(),
        required_args=frozenset(),
        answers=frozenset({"success"}),
        answers_from_arg=None,
        lands=False,
    )

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        await effects.record(Marker("in-flight", "x"))
        return Pass(choice="success")


def _graph(action: str, args: str = "") -> str:
    return f"""
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    judgement:
      prompt: |
        Assess the build.
      choices:
        pass:
          description: Complete and green.
          to: step
        fail:
          description: Incomplete.
          to: build
  step:
    executor: hub
    {"poll_interval: 30" if action == "test.hooked" else ""}
    action: {action}{args}
    judgement:
      choices:
        success:
          description: Done.
          to: done
        landed:
          description: Landed.
          to: done
        failure:
          description: Failed.
          to: build
"""


class _World:
    def __init__(self, tmp_path: Path) -> None:
        self.hooked = _Hooked()
        actions = HubActions.of(
            [*TEST_HUB_ACTIONS.implementations.values(), self.hooked, _BlockThenMark(), _Mark()]  # type: ignore[list-item]
        )
        self.hub: HubHarness = build_hub(
            tmp_path, actions=actions, action_pass_seconds=_BOUND, action_pass_grace=_GRACE
        )
        self.minted = 0

    @property
    def turns(self) -> TurnRunner:
        return self.hub.services.turns

    def at_step(self, action: str, args: str = "") -> str:
        """A chunk standing at ``step``, which names ``action``."""
        self.minted += 1
        ref = str(self.minted)
        hub = self.hub
        resp = hub.client.post("/api/graphs", json={"definition_yaml": _graph(action, args)})
        assert resp.status_code == 201, resp.text
        resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": ref})]})
        chunk_id = resp.json()["chunk_id"]
        hub.promote(chunk_id)
        claim = hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": [f"env-{ref}"]},
        )
        assert claim.status_code == 201, claim.text
        report_lease(hub, chunk_id, epoch=1, seq=1)
        build = claim.json()["envelope"]["node"]["node_id"]
        done = hub.client.post(
            f"/api/fleet/chunks/{chunk_id}/completions",
            json={
                "choice": "pass",
                "epoch": 1,
                "runner_id": "r1",
                "from_node_id": build,
                "check_results": [],
                "artifacts": [
                    {"name": "w", "kind": "git_commit", "repo": _REPO, "branch_name": "b", "commit_hash": "a" * 40}
                ],
            },
        )
        assert done.status_code == 200, done.text
        assert self.detail(chunk_id)["current_node_name"] == "step"
        return chunk_id

    def tick(self) -> None:
        self.hub.take_turns()
        self.hub.clock.advance(timedelta(seconds=1))

    def detail(self, chunk_id: str) -> dict[str, Any]:
        return self.hub.client.get(f"/api/chunks/{chunk_id}").json()

    def outcomes(self, chunk_id: str) -> list[str | None]:
        joined = s.hub_turns.join(s.flow_submissions, s.hub_turns.c.submission_id == s.flow_submissions.c.submission_id)
        with self.hub.engine.connect() as conn:
            return list(
                conn.execute(
                    hub_scoped(
                        select(s.hub_turns.c.outcome)
                        .select_from(joined)
                        .where(s.flow_submissions.c.chunk_id == chunk_id)
                        .order_by(s.hub_turns.c.started_at, s.hub_turns.c.turn_id)
                    )
                ).scalars()
            )

    def submission(self, chunk_id: str, *, epoch: int = 1) -> Submission:
        graph = self.hub.services.graphs.get(self.detail(chunk_id)["graph_id"])
        assert graph is not None
        step = graph.node_by_name("step")
        assert step is not None
        sub = self.hub.services.flows.submission_for_visit(chunk_id, node_id=step.node_id, epoch=epoch)
        assert sub is not None
        return sub

    def events(self, kind: str) -> list[Any]:
        with self.hub.engine.connect() as conn:
            return list(conn.execute(hub_scoped(select(s.event_log).where(s.event_log.c.kind == kind))).all())


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


# --- a turn that cannot end -------------------------------------------------------------------------------------


def test_a_pass_blocked_past_its_deadline_is_cut_off_routing_nothing(world: _World) -> None:
    chunk_id = world.at_step("test.block", ' \n    with: {seconds: ["1.5"]}')

    world.tick()

    assert world.outcomes(chunk_id) == ["cut-off"]
    assert world.detail(chunk_id)["current_node_name"] == "step"
    assert world.submission(chunk_id).closed is None
    (overran,) = world.events("hub-action-overran")
    assert json.loads(overran.detail)["bound_seconds"] == pytest.approx(0.6)


def test_three_cut_offs_in_a_row_escalate_the_fourth_turn_one_epoch_up(world: _World) -> None:
    chunk_id = world.at_step("test.block", ' \n    with: {seconds: ["0.8"]}')

    for _ in range(4):
        world.tick()

    assert world.outcomes(chunk_id) == ["cut-off", "cut-off", "cut-off", "escalated"]
    detail = world.detail(chunk_id)
    assert detail["status"] == "needs_human"
    with world.hub.engine.connect() as conn:
        cause, epoch = conn.execute(hub_scoped(select(s.escalations.c.cause, s.escalations.c.epoch))).one()
    assert (cause, epoch) == ("hub-action-stuck", 2)


def test_an_interrupted_turn_neither_counts_toward_the_run_nor_resets_it(world: _World) -> None:
    chunk_id = world.at_step("test.block", ' \n    with: {seconds: ["0.8"]}')
    world.tick()
    world.tick()
    with world.hub.engine.begin() as conn:  # a hub restart between the second cut-off and the next turn
        conn.execute(
            insert(s.hub_turns).values(
                stamp(
                    s.hub_turns,
                    turn_id="htn_restart",
                    submission_id=world.submission(chunk_id).submission_id,
                    started_at=world.hub.clock.now(),
                    ended_at=world.hub.clock.now(),
                    outcome="interrupted",
                )
            )
        )
    world.hub.clock.advance(timedelta(seconds=1))

    world.tick()
    world.tick()

    assert world.outcomes(chunk_id) == ["cut-off", "cut-off", "interrupted", "cut-off", "escalated"]


def test_an_effect_handed_over_after_the_deadline_raises_into_the_pass_and_writes_nothing(world: _World) -> None:
    chunk_id = world.at_step("test.block-then-mark")

    world.tick()
    asyncio.run(asyncio.sleep(1.0))  # the loop's thread comes back and hands its marker to a closed sink

    assert world.outcomes(chunk_id) == ["cut-off"]
    assert "late" not in {a.name for a in world.hub.services.chunks.artifacts.load_artifacts(chunk_id)}


def test_an_effect_write_in_flight_at_the_deadline_commits_before_the_turns_end(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunk_id = world.at_step("test.mark")
    write = TurnRunner._write_effect
    seen: list[bool] = []

    def slow(self: TurnRunner, *args: Any) -> Any:
        import time

        time.sleep(1.0)  # past the 0.6s deadline
        return write(self, *args)

    end = FlowStore.end_turn_locked

    def observed(self: FlowStore, handle: Any, turn_id: str, **kwargs: Any) -> tuple[FlowFactWritten, ...] | None:
        seen.append(
            world.hub.services.chunks.artifacts.has_hub_artifact(
                chunk_id, node_id=world.submission(chunk_id).node_id, epoch=1, name="in-flight"
            )
        )
        return end(self, handle, turn_id, **kwargs)

    monkeypatch.setattr(TurnRunner, "_write_effect", slow)
    monkeypatch.setattr(FlowStore, "end_turn_locked", observed)

    world.tick()

    assert world.outcomes(chunk_id) == ["cut-off"]
    assert seen == [True]


def test_a_forge_call_after_the_cut_off_is_refused_before_it_is_sent() -> None:
    calls: list[str] = []

    class _Adapter:
        async def branch_tip(self, branch: str) -> str | None:
            calls.append(branch)
            return None

    flag = TurnCutOff()
    gate = _BudgetGate(
        ForgeBudgets(clock=FixedClock(datetime(2026, 10, 29, tzinfo=UTC))),
        BudgetKey("frg_fixture", None),
        ForgePriority.LANDING,
    )
    refs = _RefsHandle(_Adapter(), frozenset({Capability.REFS_READ}), gate, flag)  # type: ignore[arg-type]
    flag.set()

    with pytest.raises(ForgeCallRefused):
        asyncio.run(refs.branch_tip("main"))
    assert calls == []


def test_a_build_that_raises_routes_failure_with_its_traceback(world: _World, monkeypatch: pytest.MonkeyPatch) -> None:
    chunk_id = world.at_step("test.answer", " \n    with: {choice: [success]}")

    def broken(self: TurnRunner, *args: Any) -> Any:
        raise RuntimeError("a malformed artifact")

    monkeypatch.setattr(TurnRunner, "_resolve", broken)

    world.tick()

    assert world.outcomes(chunk_id) == ["failure"]
    assert world.detail(chunk_id)["current_node_name"] == "build"
    (log,) = [
        a for a in world.hub.services.chunks.artifacts.load_artifacts(chunk_id) if a.name == "hub-log.test.answer"
    ]
    assert "a malformed artifact" in log.data


def test_a_build_held_past_its_bound_ends_its_turn_cut_off_and_routes_nothing(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunk_id = world.at_step("test.answer", " \n    with: {choice: [success]}")
    monkeypatch.setattr(world.turns, "_build_bound", 0.2)
    resolve = TurnRunner._resolve

    def slow(self: TurnRunner, *args: Any) -> Any:
        import time

        time.sleep(0.6)
        return resolve(self, *args)

    monkeypatch.setattr(TurnRunner, "_resolve", slow)

    world.tick()
    asyncio.run(asyncio.sleep(0.6))  # the build finishes, and finds its turn let go

    assert world.outcomes(chunk_id) == ["cut-off"]
    assert world.detail(chunk_id)["current_node_name"] == "step"
    assert [json.loads(e.detail)["bound"] for e in world.events("turn-interrupted")] == [0.2]


def test_a_turn_whose_end_fails_to_write_is_swept_and_its_chunk_lands_on_its_next_turn(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunk_id = world.at_step("test.answer", " \n    with: {choice: [success]}")
    end = FlowStore.end_turn_locked
    failures = [RuntimeError("connection dropped")]

    def failing(self: FlowStore, *args: Any, **kwargs: Any) -> tuple[FlowFactWritten, ...] | None:
        if failures:
            raise failures.pop()
        return end(self, *args, **kwargs)

    monkeypatch.setattr(FlowStore, "end_turn_locked", failing)

    world.tick()
    assert world.detail(chunk_id)["current_node_name"] == "step"  # the transition rolled back with the end
    world.tick()
    world.tick()

    assert world.outcomes(chunk_id) == ["interrupted", "success"]
    assert world.events("turn-interrupted")
    assert world.detail(chunk_id)["status"] == "done"


# --- levers ---------------------------------------------------------------------------------------------------------


def test_a_chunk_stopped_mid_turn_keeps_its_branch_busy_and_closes_stopped_at_the_turns_end(world: _World) -> None:
    chunks = {world.at_step("test.hooked"), world.at_step("test.hooked")}
    seen: dict[str, Any] = {}

    def stop_mid_pass(visit: Visit) -> None:
        if seen:
            return
        stopped = visit.chunk.chunk_id
        assert world.hub.client.post(f"/api/chunks/{stopped}/stop", json={}).status_code in (200, 202)
        lines = world.hub.services.flow_lines
        lines.reconcile(0)
        seen.update(chunk=stopped, admitted=lines.admit(0, world.hub.clock.now()))
        seen["closed"] = world.submission(stopped).closed

    world.hooked.hook = stop_mid_pass
    world.hooked.answer = Pass(choice="landed")

    world.tick()

    chunk_id = seen.pop("chunk")
    (waiting,) = chunks - {chunk_id}
    assert seen == {"admitted": [], "closed": None}
    assert world.outcomes(chunk_id) == ["landed"]
    assert world.submission(chunk_id).closed == "stopped"
    assert world.detail(chunk_id)["status"] == "stopped"
    world.tick()
    assert world.outcomes(waiting) == ["landed"]


def test_a_chunk_stopped_mid_turn_whose_end_fails_closes_stopped_by_the_sweep(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunk_id = world.at_step("test.hooked")

    def stop(visit: Visit) -> None:
        world.hub.client.post(f"/api/chunks/{chunk_id}/stop", json={})

    world.hooked.hook = stop
    world.hooked.answer = Pass(choice="landed")
    end = FlowStore.end_turn_locked
    failures = [RuntimeError("lock timeout")]

    def failing(self: FlowStore, *args: Any, **kwargs: Any) -> tuple[FlowFactWritten, ...] | None:
        if failures:
            raise failures.pop()
        return end(self, *args, **kwargs)

    monkeypatch.setattr(FlowStore, "end_turn_locked", failing)

    world.tick()
    world.tick()
    world.tick()

    assert world.outcomes(chunk_id) == ["interrupted"]
    assert world.submission(chunk_id).closed == "stopped"


# --- ground moved under a submission ------------------------------------------------------------------------------


def test_a_base_branch_edited_while_waiting_supersedes_the_submission_and_resubmits_on_the_new_branch(
    world: _World,
) -> None:
    chunk_id = world.at_step("test.hooked")
    world.tick()  # pending: it waits in the line of `main`
    record = world.hub.services.repository_records.list_all(include_retired=False)[0]
    world.hub.services.config_authoring.edit_repository(record, RepositoryEdit(base_branch="trunk"), OP)
    world.hub.clock.advance(timedelta(seconds=31))
    passes = world.hooked.passes

    world.tick()
    world.hub.flow_driver.wake()
    world.tick()

    assert world.hooked.passes == passes + 1  # no pass on the old submission; one on the new
    graph = world.hub.services.graphs.get(world.detail(chunk_id)["graph_id"])
    assert graph is not None
    old = world.hub.services.flows.submission_for_visit(chunk_id, node_id=world.submission(chunk_id).node_id, epoch=1)
    assert old is not None and old.generation == 1
    assert [f.branch for f in old.flows] == ["trunk"]
    assert world.outcomes(chunk_id) == ["pending", "superseded", "pending"]


def test_a_resubmitted_visit_keeps_the_moment_its_first_turn_started(world: _World) -> None:
    visits: list[Visit] = []
    world.hooked.hook = visits.append
    chunk_id = world.at_step("test.hooked")
    first_started = world.hub.clock.now()
    world.tick()
    record = world.hub.services.repository_records.list_all(include_retired=False)[0]
    world.hub.services.config_authoring.edit_repository(record, RepositoryEdit(base_branch="trunk"), OP)
    world.hub.clock.advance(timedelta(seconds=31))

    world.tick()
    world.hub.flow_driver.wake()
    world.tick()

    assert world.submission(chunk_id).generation == 1
    assert [visit.first_turn_at for visit in visits] == [first_started, first_started]
    assert visits[-1].now > first_started


# --- the terms a visit lands by -----------------------------------------------------------------------------------


def test_a_visit_built_after_an_edit_lands_by_the_terms_its_first_turn_took(world: _World) -> None:
    visits: list[Visit] = []
    world.hooked.hook = visits.append
    chunk_id = world.at_step("test.hooked")
    world.tick()
    record = world.hub.services.repository_records.list_all(include_retired=False)[0]
    project = world.hub.services.projects.get(ProjectId(default_project(world.hub.engine)))
    assert project is not None
    world.hub.services.project_authoring.order_repositories(project, [record], OP)
    world.hub.clock.advance(timedelta(seconds=31))

    world.tick()

    assert len(visits) == 2
    terms = world.hub.services.flows.terms_of(world.submission(chunk_id).submission_id)
    assert set(terms) == {record.repository_id}
    taken = terms[record.repository_id]
    assert (taken.landing_policy, taken.landing_position) == ("merge-on-checks", None)
    assert visits[-1].project.positions[record.repository_id] == 1.0
    assert [target.landing_policy for target in visits[-1].resolution.targets] == [taken.landing_policy]
    assert [target.landing_position for target in visits[-1].resolution.targets] == [None]
    assert visits[-1].now == world.hub.clock.now() - timedelta(seconds=1)  # the tick steps the clock after its pass
    assert all(row.produced_at is not None for row in visits[-1].artifacts)
    standing = [v for v in hub_invariants(world.hub.engine).run() if v.invariant == "hub:submission-terms-standing"]
    assert standing == []


def test_a_pass_that_waits_records_a_pending_wait(world: _World) -> None:
    chunk_id = world.at_step("test.hooked")

    world.tick()

    facts = world.hub.services.chunks.facts.load_facts(chunk_id)
    assert facts is not None
    assert [(poll.kind, poll.last_polled_at) for poll in facts.hub_node_polls] == [("pending", None)]
