"""``FlowDriver`` (component tier), stepped one ``tick`` at a time against a wired hub whose ``merge`` node holds its
visit with a ``pending`` landing pass. A chunk standing at the node is submitted within one tick, by whichever path
it reached it, and never twice; a submission whose chunk left the visit closes with the outcome ``withdrawal_for``
answers, and one a person holds stays open; the reconcile writes nothing to a chunk; an unchanged probe skips the
rescan until the floor; boot ends a turn its last process left open; and a raising tenant stops no other."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import func, insert, select, update

from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.hub.delivery.action_registry import HubActions
from blizzard.hub.delivery.flow_driver import FlowDriver, FlowLineStores
from blizzard.hub.domain.actions.effects import ActionEscalation, IEffectSink, Pass
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.delivery.flows import RECONCILE_FLOOR, Submission
from blizzard.hub.domain.delivery.reconcile import FlowLineReconciler
from blizzard.hub.domain.graph.actions import ActionSpec
from blizzard.hub.forge.landing import VisitForges
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.flow_store import FlowStore
from blizzard.hub.tenancy import StoreScope, TenantId
from tests.support import (
    TEST_HUB_ACTIONS,
    build_hub,
    default_tenant,
    hub_scoped,
    hub_store_connections,
    pointer_token,
    report_lease,
    stamp,
)

pytestmark = pytest.mark.component


class _HeldLanding:
    """A landing action holding its visit ``pending`` until told what to answer next."""

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
        self.passes = 0

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        self.passes += 1
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

#: ``build`` passes onto a hub node that lands nothing and answers at once, which routes the hub's own transition on.
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
        actions = HubActions.of([*TEST_HUB_ACTIONS.implementations.values(), self.action])  # type: ignore[list-item]
        self.hub = build_hub(tmp_path, actions=actions)
        self.tenant = default_tenant(self.hub.engine)
        self.stores = FlowLineStores(
            corpus=self.hub.hub_services.hub_scoped_reads.corpus,
            tenants=lambda: self.hub.hub_services.tenants.open_tenants.every().keys(),
            lines=self.lines,
        )
        self.driver = FlowDriver(self.stores, self.hub.clock)

    def lines(self, tenant: TenantId) -> FlowLineReconciler:
        return self.hub.hub_services.tenants.open_services(StoreScope(tenant)).flow_lines

    def tick(self):  # type: ignore[no-untyped-def]
        return self.driver.tick(self.stores, self.hub.clock)

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

    def complete(self, chunk_id: str, from_node_id: str, *, epoch: int = 1, choice: str = "pass") -> dict:
        resp = self.hub.client.post(
            f"/api/fleet/chunks/{chunk_id}/completions",
            json={
                "choice": choice,
                "epoch": epoch,
                "runner_id": "r1",
                "from_node_id": from_node_id,
                "check_results": [],
                "artifacts": [
                    {"name": "w", "kind": "git_commit", "repo": _REPO, "branch_name": "b", "commit_hash": "a" * 40}
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def at_merge(self, graph_yaml: str = _GRAPH, ref: str = "42") -> str:
        """A chunk held ``pending`` at ``merge`` by its first pass."""
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

    def count(self, table) -> int:  # type: ignore[no-untyped-def]
        with self.hub.engine.connect() as conn:
            return int(conn.execute(hub_scoped(select(func.count()).select_from(table))).scalar() or 0)


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


def _land(world: _World, chunk_id: str) -> None:
    """The slot's next pass lands the chunk."""
    world.action.answer = Pass(choice="landed")
    world.hub.clock.advance(timedelta(seconds=31))
    resp = world.hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")
    assert resp.json()["ran"] is True, resp.text


def _submitted_once(world: _World, chunk_id: str) -> Submission:
    """The chunk was submitted by one tick, and a forced second tick submits nothing more."""
    first = world.tick()
    assert first.submitted == 1
    world.driver.wake()
    again = world.tick()
    assert again.submitted == 0
    subs = world.submissions(chunk_id)
    assert len(subs) == 1 and subs[0].closed is None
    return subs[0]


# --- every path in -----------------------------------------------------------------------------------------------


def test_a_worker_transition_onto_the_node_is_submitted_once_to_the_branch_it_lands_on(world: _World) -> None:
    chunk_id = world.at_merge()

    sub = _submitted_once(world, chunk_id)

    record = world.hub.services.repository_records.list_all(include_retired=False)[0]
    assert [flow.resource for flow in sub.flows] == [f"repo:{record.repository_id}/main"]
    assert [fact.kind for fact in world.hub.services.flows.facts_of(sub.submission_id)] == ["submitted"]
    assert world.count(s.hub_turns) == 0


def test_a_hub_to_hub_transition_onto_the_node_is_submitted_once(world: _World) -> None:
    chunk_id = world.at_merge(_HUB_TO_HUB)
    assert world.detail(chunk_id)["current_node_name"] == "merge"

    _submitted_once(world, chunk_id)


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


def test_a_resume_at_the_node_is_submitted_once(world: _World) -> None:
    chunk_id, build = world.mint()
    assert world.hub.client.post(f"/api/chunks/{chunk_id}/pause", json={}).status_code == 202
    world.complete(chunk_id, build)
    assert world.detail(chunk_id)["status"] == "paused"
    assert world.tick().submitted == 0

    assert world.hub.client.post(f"/api/chunks/{chunk_id}/resume", json={}).status_code == 202

    _submitted_once(world, chunk_id)


def test_a_requeue_after_an_escalation_is_a_new_visit_submitted_once(world: _World) -> None:
    chunk_id = world.at_merge()
    first = _submitted_once(world, chunk_id)
    world.action.answer = Pass(choice=None, escalation=ActionEscalation(EscalationCause.FORGE_AUTH, "token revoked"))
    world.hub.clock.advance(timedelta(seconds=31))
    world.hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")
    assert world.detail(chunk_id)["status"] == "needs_human"
    world.driver.wake()
    world.tick()
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
    assert new.closed is None and new.epoch > old.epoch and new.arrival > old.arrival


# --- nothing left holding a place ---------------------------------------------------------------------------------


def test_a_chunk_the_slot_landed_closes_superseded_with_a_withdrawn_fact_on_each_flow(world: _World) -> None:
    chunk_id = world.at_merge()
    sub = _submitted_once(world, chunk_id)

    _land(world, chunk_id)
    assert world.detail(chunk_id)["status"] == "done"
    assert world.tick().closed == 1

    (closed,) = world.submissions(chunk_id)
    assert closed.closed == "superseded"
    withdrawn = [f for f in world.hub.services.flows.facts_of(sub.submission_id) if f.kind == "withdrawn"]
    assert sorted(f.flow_id for f in withdrawn) == sorted(flow.flow_id for flow in sub.flows)
    assert {f.outcome for f in withdrawn} == {"superseded"}


def test_a_stopped_chunk_closes_stopped(world: _World) -> None:
    chunk_id = world.at_merge()
    _submitted_once(world, chunk_id)

    assert world.hub.client.post(f"/api/chunks/{chunk_id}/stop", json={}).status_code in (200, 202)
    world.tick()

    assert world.submissions(chunk_id)[0].closed == "stopped"


def test_a_chunk_held_by_its_person_at_a_later_epoch_stays_open(world: _World) -> None:
    chunk_id = world.at_merge()
    _submitted_once(world, chunk_id)
    world.action.answer = Pass(choice=None, escalation=ActionEscalation(EscalationCause.FORGE_AUTH, "token revoked"))
    world.hub.clock.advance(timedelta(seconds=31))
    world.hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")
    assert world.detail(chunk_id)["status"] == "needs_human"
    world.driver.wake()
    world.tick()
    assert world.submissions(chunk_id)[0].closed is None

    # Paused by the person holding it, then requeued: it stands paused at the node, at the later epoch.
    assert world.hub.client.post(f"/api/chunks/{chunk_id}/pause", json={}).status_code == 202
    world.hub.clock.advance(timedelta(seconds=1))
    assert world.hub.client.post(f"/api/chunks/{chunk_id}/requeues").status_code == 202
    assert world.detail(chunk_id)["status"] == "paused"
    world.driver.wake()
    result = world.tick()

    assert (result.submitted, result.closed) == (0, 0)
    assert [sub.closed for sub in world.submissions(chunk_id)] == [None]


# --- the reconcile writes nothing to a chunk ----------------------------------------------------------------------


def test_a_visit_the_slot_would_refuse_is_left_unsubmitted_and_refused_once_by_the_slot(world: _World) -> None:
    chunk_id, build = world.mint()
    resp = world.hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": 1,
            "runner_id": "r1",
            "from_node_id": build,
            "check_results": [],
            "artifacts": [
                {"name": "w", "kind": "git_commit", "repo": "acme/unknown", "branch_name": "b", "commit_hash": "a" * 40}
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    refusals = world.count(s.event_log)

    world.tick()
    world.driver.wake()
    world.tick()

    assert world.submissions(chunk_id) == []
    assert world.count(s.event_log) == refusals
    hub_logs = [
        a.name for a in world.hub.services.chunks.artifacts.load_artifacts(chunk_id) if a.name.startswith("hub-log")
    ]
    assert len(hub_logs) == 1


def test_a_chunk_at_an_actionless_node_is_escalated_by_the_boot_pass_never_by_the_reconcile(world: _World) -> None:
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
    assert world.count(s.escalations) == 0

    assert world.hub.services.hub_node.escalate_actionless() == 1
    world.driver.wake()
    world.tick()
    assert world.count(s.escalations) == 1
    assert world.submissions(chunk_id) == []


# --- the probe -----------------------------------------------------------------------------------------------------


def test_an_unchanged_signature_skips_the_rescan_until_the_floor(world: _World) -> None:
    world.at_merge()
    assert world.tick().reconciled == (world.tenant,)
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
    assert [(f.kind, f.outcome) for f in world.hub.services.flows.facts_of(sub.submission_id)] == [
        ("submitted", None),
        ("turn-ended", "interrupted"),
    ]
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


# --- isolation ------------------------------------------------------------------------------------------------------


def test_a_raising_tenant_does_not_stop_the_others(world: _World) -> None:
    other = world.hub.add_tenant("other")

    def lines(tenant: TenantId) -> FlowLineReconciler:
        if tenant == world.tenant:
            raise RuntimeError("store unavailable")
        return world.lines(tenant)

    stores = FlowLineStores(corpus=world.stores.corpus, tenants=world.stores.tenants, lines=lines)
    result = FlowDriver(stores, world.hub.clock).tick(stores, world.hub.clock)

    assert result.failed == (world.tenant,)
    assert result.reconciled == (other.tenant,)


def test_a_failing_tick_skips_its_cycle_and_never_ends_run(world: _World) -> None:
    ticks: list[int] = []

    def tenants() -> list[TenantId]:
        ticks.append(1)
        raise RuntimeError("probe unavailable")

    stores = FlowLineStores(corpus=world.stores.corpus, tenants=tenants, lines=world.lines)
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
