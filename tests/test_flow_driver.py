"""``FlowDriver`` (component tier), stepped one tick at a time — each tick's turns run to their end — against a wired
hub whose ``merge`` node lands with an action holding its visit ``pending`` until told otherwise. Covers every path onto
the node, the line's order and its yields, the reconcile's refusals and closures, the sweep, boot, and isolation."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import func, insert, inspect, select, update

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.hub.config import HubConfig
from blizzard.hub.delivery.action_registry import HubActions
from blizzard.hub.delivery.flow_driver import FlowDriver, FlowLineStores, TickResult
from blizzard.hub.domain.actions.effects import ActionEscalation, IEffectSink, Pass
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.model import WorkRef, decide_close_enqueue
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.delivery.flows import RECONCILE_FLOOR, Submission
from blizzard.hub.domain.delivery.reconcile import FlowLineReconciler
from blizzard.hub.domain.graph.actions import ActionSpec
from blizzard.hub.forge.budget import BudgetKey, ForgeBudgetLow, ForgeBudgets, ForgePriority
from blizzard.hub.forge.budget_notices import BudgetNoticeDrain
from blizzard.hub.forge.landing import VisitForges
from blizzard.hub.runtime import Migrations
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.flow_store import FlowStore
from blizzard.hub.tenancy import StoreScope, TenantId
from tests.support import (
    TEST_HUB_ACTIONS,
    build_hub,
    chunk_stores,
    default_tenant,
    hub_scoped,
    hub_store_connections,
    pointer_token,
    report_lease,
    stamp,
)

pytestmark = pytest.mark.component


class _HeldLanding:
    """A landing action answering ``answer`` — ``pending`` until told otherwise."""

    spec = ActionSpec(
        name="test.held-landing",
        args=frozenset(),
        required_args=frozenset(),
        answers=frozenset({"pending", "landed", "failure"}),
        answers_from_arg=None,
        lands=True,
    )

    def __init__(self) -> None:
        self.answer = Pass(choice="pending")
        self.passes: list[str] = []

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        self.passes.append(visit.chunk.chunk_id)
        return self.answer


_GRAPH = """
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
          to: merge
        fail:
          description: Incomplete.
          to: build
  merge:
    executor: hub
    poll_interval: 30
    action: test.held-landing
    judgement:
      choices:
        landed:
          description: Landed.
          to: done
        failure:
          description: Failed to land.
          to: build
"""

#: ``build`` passes onto a hub node that lands nothing and answers at once, whose turn routes the hub on.
_HUB_TO_HUB = _GRAPH.replace("          to: merge\n", "          to: check\n", 1).replace(
    "  merge:\n",
    """  check:
    executor: hub
    action: test.answer
    with: {choice: [success]}
    judgement:
      choices:
        success:
          description: Checked.
          to: merge
        failure:
          description: Failed.
          to: build
  merge:
""",
    1,
)

#: A graph a cross-graph edge lands on: its entry is the held landing node.
_LANDING = """
name: landing
entry: merge
nodes:
  merge:
    executor: hub
    poll_interval: 30
    action: test.held-landing
    judgement:
      choices:
        landed:
          description: Landed.
          to: done
        failure:
          description: Failed to land.
          to: done
"""

_REPO = "acme/widget"


class _World:
    def __init__(self, tmp_path: Path) -> None:
        self.action = _HeldLanding()
        actions = HubActions.of([*TEST_HUB_ACTIONS.implementations.values(), self.action])
        self.hub = build_hub(tmp_path, actions=actions)
        self.tenant = default_tenant(self.hub.engine)
        self.driver = self.hub.flow_driver
        self.stores = FlowLineStores(
            corpus=self.hub.hub_services.hub_scoped_reads.corpus,
            tenants=lambda: self.hub.hub_services.tenants.open_tenants.every().keys(),
            lines=self.lines,
            turns=lambda tenant: self.hub.open_services(tenant).turns,
            notices=lambda tenant: BudgetNoticeDrain(
                self.hub.open_services(tenant).forge_budgets, self.hub.open_services(tenant).event_log
            ),
            publishers=lambda tenant: self.hub.open_services(tenant).events,
        )

    def lines(self, tenant: TenantId) -> FlowLineReconciler:
        return self.hub.hub_services.tenants.open_services(StoreScope(tenant)).flow_lines

    def tick(self) -> TickResult:
        """One tick, its turns run to their end."""
        (result,) = self.hub.take_turns()
        return result

    def mint(self, graph_yaml: str = _GRAPH, ref: str = "42") -> tuple[str, str]:
        """A promoted, claimed chunk at ``build``, epoch 1: its id and ``build``'s node id."""
        hub = self.hub
        hub.client.post("/api/graphs", json={"definition_yaml": graph_yaml})
        resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": ref})]})
        assert resp.status_code == 201, resp.text
        chunk_id = resp.json()["chunk_id"]
        hub.promote(chunk_id)
        claim = hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": [f"env-{ref}"]},
        )
        assert claim.status_code == 201, claim.text
        report_lease(hub, chunk_id, epoch=1, seq=1)
        return chunk_id, claim.json()["envelope"]["node"]["node_id"]

    def complete(self, chunk_id: str, from_node_id: str, *, repo: str = _REPO) -> dict:
        resp = self.hub.client.post(
            f"/api/fleet/chunks/{chunk_id}/completions",
            json={
                "choice": "pass",
                "epoch": 1,
                "runner_id": "r1",
                "from_node_id": from_node_id,
                "check_results": [],
                "artifacts": [
                    {"name": "w", "kind": "git_commit", "repo": repo, "branch_name": "b", "commit_hash": "a" * 40}
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def at_merge(self, graph_yaml: str = _GRAPH, ref: str = "42") -> str:
        """A chunk standing at ``merge``, not yet submitted."""
        chunk_id, build = self.mint(graph_yaml, ref)
        self.complete(chunk_id, build)
        assert self.detail(chunk_id)["status"] == "delivering"
        return chunk_id

    def detail(self, chunk_id: str) -> dict:
        return self.hub.client.get(f"/api/chunks/{chunk_id}").json()

    def submissions(self, chunk_id: str) -> list[Submission]:
        """Every submission of the chunk, open or closed, in arrival order."""
        flows = self.hub.services.flows
        with self.hub.engine.connect() as conn:
            rows = conn.execute(
                hub_scoped(
                    select(s.flow_submissions.c.node_id, s.flow_submissions.c.epoch)
                    .where(s.flow_submissions.c.chunk_id == chunk_id)
                    .order_by(s.flow_submissions.c.arrival)
                )
            ).all()
        found = [flows.submission_for_visit(chunk_id, node_id=r.node_id, epoch=r.epoch) for r in rows]
        return [sub for sub in found if sub is not None]

    def turn_outcomes(self, chunk_id: str) -> list[str | None]:
        """Every turn the chunk took, oldest first."""
        joined = s.hub_turns.join(s.flow_submissions, s.hub_turns.c.submission_id == s.flow_submissions.c.submission_id)
        with self.hub.engine.connect() as conn:
            return list(
                conn.execute(
                    hub_scoped(
                        select(s.hub_turns.c.outcome)
                        .select_from(joined)
                        .where(s.flow_submissions.c.chunk_id == chunk_id)
                        .order_by(s.hub_turns.c.turn_id)
                    )
                ).scalars()
            )

    def count(self, table) -> int:
        with self.hub.engine.connect() as conn:
            return int(conn.execute(hub_scoped(select(func.count()).select_from(table))).scalar() or 0)

    def poll_due(self) -> None:
        """Past every waiting visit's poll interval."""
        self.hub.clock.advance(timedelta(seconds=31))


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


def _submitted_once(world: _World, chunk_id: str) -> Submission:
    """The chunk was submitted and took its turn on one tick, and a forced second tick submits nothing more."""
    first = world.tick()
    assert first.submitted == 1
    world.driver.wake()
    again = world.tick()
    assert again.submitted == 0
    subs = world.submissions(chunk_id)
    assert len(subs) == 1 and subs[0].closed is None
    return subs[0]


# --- every path in -----------------------------------------------------------------------------------------------


def test_a_worker_transition_onto_the_node_is_submitted_once_and_takes_its_turn(world: _World) -> None:
    chunk_id = world.at_merge()

    sub = _submitted_once(world, chunk_id)

    record = world.hub.services.repository_records.list_all(include_retired=False)[0]
    assert [flow.resource for flow in sub.flows] == [f"repo:{record.repository_id}/main"]
    assert [(f.kind, f.outcome) for f in world.hub.services.flows.facts_of(sub.submission_id)] == [
        ("submitted", None),
        ("turn-started", None),
        ("turn-ended", "pending"),
    ]
    assert world.action.passes == [chunk_id]


def test_a_hub_to_hub_transition_is_routed_by_a_turn_and_the_next_node_submitted_once(world: _World) -> None:
    chunk_id = world.at_merge(_HUB_TO_HUB)
    assert world.detail(chunk_id)["current_node_name"] == "check"

    world.tick()
    assert world.detail(chunk_id)["current_node_name"] == "merge"
    (check,) = world.submissions(chunk_id)
    assert check.closed == "success" and check.flows == ()

    world.driver.wake()
    assert world.tick().submitted == 1
    assert [sub.closed for sub in world.submissions(chunk_id)] == ["success", None]


def test_a_restart_onto_the_node_is_submitted_once(world: _World) -> None:
    chunk_id, _build = world.mint()
    world.hub.clock.advance(timedelta(seconds=1))
    resp = world.hub.client.post(f"/api/chunks/{chunk_id}/restart", json={"node": "merge"})
    assert resp.status_code == 202, resp.text
    assert world.detail(chunk_id)["current_node_name"] == "merge"

    _submitted_once(world, chunk_id)


def test_a_cross_graph_migration_onto_the_node_is_submitted_once(world: _World) -> None:
    assert world.hub.client.post("/api/graphs", json={"definition_yaml": _LANDING}).status_code == 201
    chunk_id, build = world.mint(_GRAPH.replace("          to: merge\n", "          to: graph:landing\n", 1))
    world.complete(chunk_id, build)
    detail = world.detail(chunk_id)
    assert (detail["status"], detail["current_node_name"]) == ("delivering", "merge")

    _submitted_once(world, chunk_id)


def test_a_chunk_paused_at_the_node_takes_no_turn_until_it_is_resumed(world: _World) -> None:
    chunk_id, build = world.mint()
    assert world.hub.client.post(f"/api/chunks/{chunk_id}/pause", json={}).status_code == 202
    world.complete(chunk_id, build)
    assert world.detail(chunk_id)["status"] == "paused"
    assert (world.tick().submitted, world.action.passes) == (0, [])

    assert world.hub.client.post(f"/api/chunks/{chunk_id}/resume", json={}).status_code == 202

    _submitted_once(world, chunk_id)
    assert world.action.passes == [chunk_id]


def test_a_requeue_after_an_escalation_is_a_new_visit_at_the_back(world: _World) -> None:
    chunk_id = world.at_merge()
    world.action.answer = Pass(choice=None, escalation=ActionEscalation(EscalationCause.FORGE_AUTH, "token revoked"))
    first = _submitted_once(world, chunk_id)
    assert world.detail(chunk_id)["status"] == "needs_human"
    assert world.turn_outcomes(chunk_id) == ["escalated"]
    assert world.submissions(chunk_id)[0].closed is None  # a person holds it at a later epoch

    world.hub.clock.advance(timedelta(seconds=1))
    assert world.hub.client.post(f"/api/chunks/{chunk_id}/requeues").status_code == 202
    world.action.answer = Pass(choice="pending")
    assert world.detail(chunk_id)["status"] == "delivering"
    assert world.tick().submitted == 1
    world.driver.wake()
    assert world.tick().submitted == 0

    old, new = world.submissions(chunk_id)
    assert old.submission_id == first.submission_id and old.closed == "superseded"
    assert new.closed is None and new.epoch == old.epoch + 1 and new.arrival > old.arrival


# --- the line -------------------------------------------------------------------------------------------------------


def test_two_chunks_on_one_branch_take_turns_in_arrival_order_and_a_pending_one_yields(world: _World) -> None:
    first = world.at_merge(ref="first")
    world.hub.clock.advance(timedelta(seconds=1))
    second = world.at_merge(ref="second")

    world.tick()
    assert world.action.passes == [first]  # one turn at a time on the branch, the earlier arrival first

    world.driver.wake()
    world.tick()
    assert world.action.passes == [first, second]  # the first waits on its poll; the branch is the second's

    world.driver.wake()
    world.tick()
    assert world.action.passes == [first, second]  # neither is due again before its poll interval

    world.action.answer = Pass(choice="landed")
    world.poll_due()
    world.tick()
    world.driver.wake()
    world.tick()
    assert world.action.passes == [first, second, first, second]
    assert [world.submissions(c)[0].closed for c in (first, second)] == ["landed", "landed"]


def test_a_chunk_that_lands_closes_with_its_verdict(world: _World) -> None:
    chunk_id = world.at_merge()
    world.action.answer = Pass(choice="landed")

    world.tick()

    assert world.detail(chunk_id)["status"] == "done"
    (sub,) = world.submissions(chunk_id)
    assert sub.closed == "landed"
    facts = [(f.kind, f.outcome) for f in world.hub.services.flows.facts_of(sub.submission_id)]
    assert facts == [("submitted", None), ("turn-started", None), ("turn-ended", "landed"), ("withdrawn", "landed")]


def test_hub_advance_wakes_the_driver_and_answers_where_the_visit_stands(world: _World) -> None:
    first = world.at_merge(ref="first")
    world.hub.clock.advance(timedelta(seconds=1))
    second = world.at_merge(ref="second")
    advance = f"/api/fleet/chunks/{first}/hub-advance"

    unsubmitted = world.hub.client.post(advance).json()
    assert (unsubmitted["ran"], unsubmitted["outcome_choice"]) == (False, None)
    assert unsubmitted["detail"] == "waiting to be submitted to its flows"
    world.driver.tick(world.stores, world.hub.clock)  # submitted, and the first's turn taken but not yet run

    assert world.hub.client.post(advance).json()["detail"] == "landing on widget/main"
    assert world.hub.client.post(f"/api/fleet/chunks/{second}/hub-advance").json()["detail"] == "2nd on widget/main"


def test_a_pending_visit_is_reported_waiting_on_its_poll(world: _World) -> None:
    chunk_id = world.at_merge()
    world.tick()

    detail = world.hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance").json()["detail"]

    assert detail.startswith("pending — next poll at ")


# --- nothing left holding a place ---------------------------------------------------------------------------------


def test_a_stopped_chunk_closes_stopped(world: _World) -> None:
    chunk_id = world.at_merge()
    _submitted_once(world, chunk_id)

    assert world.hub.client.post(f"/api/chunks/{chunk_id}/stop", json={}).status_code in (200, 202)
    world.tick()

    assert world.submissions(chunk_id)[0].closed == "stopped"


def test_a_chunk_held_by_its_person_at_a_later_epoch_stays_open_and_takes_no_turn(world: _World) -> None:
    chunk_id = world.at_merge()
    world.action.answer = Pass(choice=None, escalation=ActionEscalation(EscalationCause.FORGE_AUTH, "token revoked"))
    _submitted_once(world, chunk_id)
    assert world.detail(chunk_id)["status"] == "needs_human"

    # Paused by the person holding it, then requeued: it stands paused at the node, at the later epoch.
    assert world.hub.client.post(f"/api/chunks/{chunk_id}/pause", json={}).status_code == 202
    world.hub.clock.advance(timedelta(seconds=1))
    assert world.hub.client.post(f"/api/chunks/{chunk_id}/requeues").status_code == 202
    assert world.detail(chunk_id)["status"] == "paused"
    world.driver.wake()
    result = world.tick()

    assert (result.submitted, result.closed, result.started) == (0, 0, ())
    assert [sub.closed for sub in world.submissions(chunk_id)] == [None]


# --- the reconcile's own writes -----------------------------------------------------------------------------------


def test_a_visit_that_cannot_name_its_resources_is_refused_at_submit_once(world: _World) -> None:
    chunk_id, build = world.mint()
    world.complete(chunk_id, build, repo="acme/unknown")

    world.tick()
    world.driver.wake()
    world.tick()

    assert world.submissions(chunk_id) == []
    assert world.detail(chunk_id)["current_node_name"] == "build"
    hub_logs = [
        a.name for a in world.hub.services.chunks.artifacts.load_artifacts(chunk_id) if a.name.startswith("hub-log")
    ]
    assert hub_logs == ["hub-log.repository-unresolved"]
    with world.hub.engine.connect() as conn:
        kinds = conn.execute(hub_scoped(select(s.event_log.c.kind).where(s.event_log.c.chunk_id == chunk_id))).all()
    assert [k for (k,) in kinds].count("repository-unresolved") == 1


def test_a_chunk_at_an_actionless_node_is_escalated_by_the_reconcile_one_epoch_up(world: _World) -> None:
    chunk_id, build = world.mint()
    graph_id = world.detail(chunk_id)["graph_id"]
    with world.hub.engine.begin() as conn:  # as the carry-over leaves a node it could not translate
        conn.execute(
            hub_scoped(
                update(s.graph_nodes)
                .where(s.graph_nodes.c.graph_id == graph_id, s.graph_nodes.c.name == "merge")
                .values(action=None, action_args=None, legacy_run="make land")
            )
        )
    graph = world.hub.services.graphs.get(graph_id)
    assert graph is not None
    merge = graph.node_by_name("merge")
    assert merge is not None
    cast(IWriteChunkMovementRepository, world.hub.services.chunks.movement).record_transition(
        transition_id="tr_parked",
        close_decision=decide_close_enqueue([WorkRef("default", "42")], lambda _: True),
        chunk_id=chunk_id,
        from_node_id=build,
        to_node_id=merge.node_id,
        choice_name="pass",
        epoch=1,
        runner_id="r1",
        at=world.hub.clock.now(),
        artifacts=[],
        admission=EpochAdmission.AT_OR_ABOVE,
    )

    world.tick()
    world.driver.wake()
    world.tick()

    with world.hub.engine.connect() as conn:
        (epoch,) = conn.execute(hub_scoped(select(s.escalations.c.epoch))).one()
    assert epoch == 2
    assert world.submissions(chunk_id) == []
    assert world.lines(world.tenant).escalate_actionless() == 0


# --- the probe -----------------------------------------------------------------------------------------------------


def test_an_unchanged_signature_skips_the_rescan_until_the_floor(world: _World) -> None:
    world.at_merge()
    assert world.tick().reconciled == (world.tenant,)
    world.tick()  # the wake its turn's end sent
    # A submission no chunk stands behind, written where the probe does not look: only a full rescan closes it.
    ghost = FlowStore(hub_store_connections(world.hub.engine), world.hub.clock).submit(
        "ch_ghost", node_id="nd_gone", epoch=1, generation=0, resources=[], at=world.hub.clock.now()
    )
    assert ghost is not None

    world.hub.clock.advance(RECONCILE_FLOOR - timedelta(seconds=1))
    skipped = world.tick()
    assert (skipped.skipped, skipped.closed) == ((world.tenant,), 0)

    world.hub.clock.advance(timedelta(seconds=1))
    floor = world.tick()
    assert (floor.reconciled, floor.closed) == ((world.tenant,), 1)


# --- a turn no task answers for -----------------------------------------------------------------------------------


def test_a_turn_a_tick_started_but_no_task_ran_is_ended_by_the_sweep_within_two_ticks(world: _World) -> None:
    chunk_id = world.at_merge()
    orphaned = world.driver.tick(world.stores, world.hub.clock)  # the turn is taken; its task is never created
    assert len(orphaned.started) == 1

    world.hub.clock.advance(timedelta(seconds=2))
    assert world.tick().interrupted == 0  # it started at the previous tick, which may still be creating its task
    world.hub.clock.advance(timedelta(seconds=2))
    swept = world.tick()

    assert swept.interrupted == 1
    assert world.turn_outcomes(chunk_id)[0] == "interrupted"
    with world.hub.engine.connect() as conn:
        assert conn.execute(hub_scoped(select(s.event_log).where(s.event_log.c.kind == "turn-interrupted"))).first()
    world.tick()
    assert world.action.passes == [chunk_id]  # eligible again at once, and taken by a fresh turn


def test_a_chunk_a_person_takes_after_its_turn_is_taken_gets_no_pass(world: _World) -> None:
    chunk_id = world.at_merge()
    (started,) = world.driver.tick(world.stores, world.hub.clock).started
    stores = chunk_stores(world.hub.engine, world.hub.clock)
    with stores.exclusive.locked([chunk_id]) as handle:
        stores.escalations.record_bounce_escalation_locked(
            handle,
            chunk_id,
            epoch=2,
            admission=EpochAdmission.ABOVE,
            runner_id="hub",
            takeover_command="",
            at=world.hub.clock.now(),
            cause=EscalationCause.FORGE_AUTH,
            detail="held by a person",
        )
    assert world.detail(chunk_id)["status"] == "needs_human"

    asyncio.run(world.stores.turns(world.tenant).take(started.turn, started.submission))

    assert world.action.passes == []
    assert world.turn_outcomes(chunk_id) == ["superseded"]
    assert world.submissions(chunk_id)[0].closed is None  # a person holds it at a later epoch


def test_a_running_turn_is_never_swept(world: _World) -> None:
    world.at_merge()
    taken = world.driver.tick(world.stores, world.hub.clock)
    (started,) = taken.started
    world.hub.clock.advance(timedelta(seconds=2))
    world.driver.tick(world.stores, world.hub.clock, running=frozenset({started.turn.turn_id}))
    world.hub.clock.advance(timedelta(seconds=2))

    result = world.driver.tick(world.stores, world.hub.clock, running=frozenset({started.turn.turn_id}))

    assert result.interrupted == 0
    assert [t.turn_id for t in world.hub.services.flows.open_turns()] == [started.turn.turn_id]


# --- boot -----------------------------------------------------------------------------------------------------------


def test_boot_ends_a_planted_open_turn_interrupted_once(world: _World) -> None:
    chunk_id = world.at_merge()
    sub = _submitted_once(world, chunk_id)
    with world.hub.engine.begin() as conn:
        conn.execute(
            insert(s.hub_turns).values(
                stamp(s.hub_turns, turn_id="htn_1", submission_id=sub.submission_id, started_at=world.hub.clock.now())
            )
        )

    assert world.driver.recover_turns(world.tenant) == 1
    events, facts = world.count(s.event_log), world.count(s.flow_facts)
    assert FlowDriver(world.stores, world.hub.clock).recover_turns(world.tenant) == 0

    assert (world.count(s.event_log), world.count(s.flow_facts)) == (events, facts)
    assert [(f.kind, f.outcome) for f in world.hub.services.flows.facts_of(sub.submission_id)][-1] == (
        "turn-ended",
        "interrupted",
    )
    with world.hub.engine.connect() as conn:
        event = conn.execute(hub_scoped(select(s.event_log).where(s.event_log.c.kind == "turn-interrupted"))).one()
    assert (event.severity, event.chunk_id, event.node_name) == ("warning", chunk_id, "merge")
    assert json.loads(event.detail)["turn_id"] == "htn_1"
    assert world.submissions(chunk_id)[0].closed is None  # its visit is current: it keeps its place


def test_the_first_boot_submits_chunks_mid_delivery_in_the_order_they_entered_their_node(world: _World) -> None:
    later, build_later = world.mint(ref="later")
    earlier, build_earlier = world.mint(ref="earlier")
    world.complete(earlier, build_earlier)
    world.hub.clock.advance(timedelta(seconds=1))
    world.complete(later, build_later)

    world.driver.recover_turns(world.tenant)

    (first,), (second,) = world.submissions(earlier), world.submissions(later)
    assert first.arrival < second.arrival


# --- forge budget notices -----------------------------------------------------------------------------------------


def _budget_events(world: _World) -> list[tuple[str, str, dict]]:
    with world.hub.engine.connect() as conn:
        rows = conn.execute(
            hub_scoped(
                select(s.event_log.c.kind, s.event_log.c.severity, s.event_log.c.detail)
                .where(s.event_log.c.kind.in_(("forge-budget-low", "forge-rate-limited")))
                .order_by(s.event_log.c.id)
            )
        ).all()
    return [(r.kind, r.severity, r.detail if isinstance(r.detail, dict) else json.loads(r.detail)) for r in rows]


def test_the_tick_drains_each_budget_notice_once_per_window_into_the_tenants_log(world: _World) -> None:
    budgets = world.hub.open_services(world.tenant).forge_budgets
    key = BudgetKey("github", "token")
    reset = world.hub.clock.now() + timedelta(minutes=2)
    headers = {
        "X-RateLimit-Limit": "100",
        "X-RateLimit-Remaining": "30",
        "X-RateLimit-Reset": str(int(reset.timestamp())),
    }
    budgets.learn(key, headers)

    for _ in range(2):
        with pytest.raises(ForgeBudgetLow):
            budgets.admit(key, ForgePriority.SWEEPING)
    budgets.spent(key, reset)
    budgets.spent(key, reset)
    first = world.tick()
    with pytest.raises(ForgeBudgetLow):
        budgets.admit(key, ForgePriority.SWEEPING)
    second = world.tick()

    assert (first.noticed, second.noticed) == (2, 0)
    events = _budget_events(world)
    assert [(kind, severity) for kind, severity, _ in events] == [
        ("forge-budget-low", "warning"),
        ("forge-rate-limited", "warning"),
    ]
    assert events[0][2] == {
        "forge": "github",
        "secret": "token",
        "bucket": "core",
        "remaining": 30,
        "limit": 100,
        "reset": reset.replace(microsecond=0).isoformat(),
    }


def test_a_new_window_drains_its_notice_again(world: _World) -> None:
    budgets = world.hub.open_services(world.tenant).forge_budgets
    key = BudgetKey("github", None)
    for _ in range(2):
        reset = world.hub.clock.now() + timedelta(minutes=1)
        budgets.spent(key, reset)
        world.tick()
        world.hub.clock.advance(timedelta(minutes=2))

    assert [kind for kind, _, _ in _budget_events(world)] == ["forge-rate-limited", "forge-rate-limited"]


def test_a_failed_notice_write_requeues_it_and_the_ones_after_for_the_next_drain() -> None:
    clock = FixedClock(datetime(2026, 10, 29, 12, 0, tzinfo=UTC))
    budgets = ForgeBudgets(clock=clock)
    key = BudgetKey("github", None)
    budgets.spent(key, clock.now() + timedelta(minutes=1))
    budgets.learn(
        key,
        {
            "X-RateLimit-Limit": "100",
            "X-RateLimit-Remaining": "10",
            "X-RateLimit-Reset": str(int((clock.now() + timedelta(minutes=1)).timestamp())),
        },
    )
    with pytest.raises(ForgeBudgetLow):
        budgets.admit(key, ForgePriority.SWEEPING)

    class _Log:
        def __init__(self) -> None:
            self.kinds: list[str] = []
            self.fail = True

        def record(self, **kwargs: Any) -> None:
            if self.fail:
                self.fail = False
                raise RuntimeError("store unavailable")
            self.kinds.append(kwargs["kind"])

    log = _Log()
    drain = BudgetNoticeDrain(budgets, cast(EventLogService, log))
    with pytest.raises(RuntimeError):
        drain.drain(clock.now())
    recorded = drain.drain(clock.now())

    assert (recorded, log.kinds) == (2, ["forge-rate-limited", "forge-budget-low"])


def test_a_store_carried_across_the_flows_revision_boots_pending_visits_in_entry_order(
    world: _World, tmp_path: Path
) -> None:
    later, build_later = world.mint(ref="later")
    world.hub.clock.advance(timedelta(seconds=1))
    earlier, build_earlier = world.mint(ref="earlier")
    world.complete(earlier, build_earlier)
    world.hub.clock.advance(timedelta(seconds=1))
    world.complete(later, build_later)
    assert later < earlier
    assert world.submissions(earlier) == world.submissions(later) == []

    world.hub.engine.dispose()
    migrations = Migrations(HubConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'hub.db'}"), world.hub.clock).runner
    migrations.downgrade("20261025_0900_forges")
    assert s.flow_submissions.name not in inspect(world.hub.engine).get_table_names()
    migrations.upgrade("head")

    action = _HeldLanding()
    actions = HubActions.of([*TEST_HUB_ACTIONS.implementations.values(), action])
    reopened = build_hub(tmp_path, actions=actions)
    driver = reopened.flow_driver
    driver.recover_turns(default_tenant(reopened.engine))
    with reopened.engine.connect() as conn:
        rows = conn.execute(
            hub_scoped(
                select(s.flow_submissions.c.chunk_id, s.flow_submissions.c.arrival).order_by(
                    s.flow_submissions.c.arrival
                )
            )
        ).all()
    assert [row.chunk_id for row in rows] == [earlier, later]
    assert rows[0].arrival < rows[1].arrival
    assert [reopened.client.get(f"/api/chunks/{chunk}").json()["status"] for chunk in (earlier, later)] == [
        "delivering",
        "delivering",
    ]


# --- isolation ------------------------------------------------------------------------------------------------------


def test_a_raising_tenant_does_not_stop_the_others(world: _World) -> None:
    other = world.hub.add_tenant("other")

    def lines(tenant: TenantId) -> FlowLineReconciler:
        if tenant == world.tenant:
            raise RuntimeError("store unavailable")
        return world.lines(tenant)

    stores = FlowLineStores(
        corpus=world.stores.corpus,
        tenants=world.stores.tenants,
        lines=lines,
        turns=world.stores.turns,
        notices=world.stores.notices,
        publishers=world.stores.publishers,
    )
    result = FlowDriver(stores, world.hub.clock).tick(stores, world.hub.clock)

    assert result.failed == (world.tenant,)
    assert result.reconciled == (other.tenant,)


def test_a_failing_tick_skips_its_cycle_and_never_ends_run(world: _World) -> None:
    ticks: list[int] = []

    def tenants() -> list[TenantId]:
        ticks.append(1)
        raise RuntimeError("probe unavailable")

    stores = FlowLineStores(
        corpus=world.stores.corpus,
        tenants=tenants,
        lines=world.lines,
        turns=world.stores.turns,
        notices=world.stores.notices,
        publishers=world.stores.publishers,
    )
    driver = FlowDriver(stores, world.hub.clock)

    async def drive() -> None:
        shutdown = asyncio.Event()
        running = asyncio.create_task(driver.run(shutdown))
        await asyncio.sleep(0.1)
        driver.wake()  # cut short, but no sooner than a second after the failed tick
        await asyncio.sleep(1.2)
        shutdown.set()
        await running

    asyncio.run(drive())

    assert len(ticks) == 2


def test_a_visit_that_raises_is_skipped_and_the_tenants_other_visits_still_submit(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken, build_broken = world.mint(ref="broken")
    sound, build_sound = world.mint(ref="sound")
    world.complete(broken, build_broken)
    world.complete(sound, build_sound)
    submit = FlowLineReconciler._submit

    def flaky(self: FlowLineReconciler, standing):
        if standing.chunk.chunk_id == broken:
            raise RuntimeError("unreadable visit")
        return submit(self, standing)

    monkeypatch.setattr(FlowLineReconciler, "_submit", flaky)

    result = world.tick()

    assert result.failed == () and result.reconciled == (world.tenant,)
    assert world.submissions(broken) == [] and len(world.submissions(sound)) == 1
