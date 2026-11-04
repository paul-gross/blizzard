"""``FlowDriver`` (component tier), stepped one tick at a time — each tick's turns run to their end — against a wired
hub whose ``merge`` node lands with an action holding its visit ``pending`` until told otherwise. Covers every path onto
the node, the line's order and its yields, the reconcile's refusals and closures, the sweep, boot, and isolation."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import insert, inspect, select, update

from blizzard.foundation.clock import ControlledClock, FixedClock
from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.hub.api.clock import clock_router
from blizzard.hub.api.test_flow_control import controlled_flow_router
from blizzard.hub.config import HubConfig
from blizzard.hub.delivery import flow_turn
from blizzard.hub.delivery.action_registry import HubActions
from blizzard.hub.delivery.flow_driver import FlowDriver, FlowLineStores
from blizzard.hub.delivery.flow_turn import TurnEndRefused
from blizzard.hub.delivery.hub_node import HubNodeRouter
from blizzard.hub.domain.actions.effects import ActionEscalation, IEffectSink, Pass
from blizzard.hub.domain.actions.visit import Visit, resolve_visit
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.model import WorkRef, decide_close_enqueue
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.delivery.flows import RECONCILE_FLOOR, Submission, Withdrawal
from blizzard.hub.domain.delivery.reconcile import (
    FlowLineReconciler,
    _Standing,  # pyright: ignore[reportPrivateUsage]  # the patched submit seam is typed by this private record
)
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.forge.budget import BudgetKey, BudgetReading, ForgeBudgetLow, ForgeBudgets, ForgePriority
from blizzard.hub.forge.budget_notices import BudgetNoticeDrain
from blizzard.hub.forge.landing import VisitForges
from blizzard.hub.forge.rates import rate_dialect
from blizzard.hub.runtime import Migrations
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_escalations_store import ChunkEscalationsStore
from blizzard.hub.store.internal.chunk_events_store import ChunkEventsStore
from blizzard.hub.store.internal.chunk_hub_exec_store import ChunkHubExecStore
from blizzard.hub.store.internal.chunk_movement_store import ChunkMovementStore
from blizzard.hub.store.internal.flow_store import FlowStore
from blizzard.hub.tenancy import TenantId
from blizzard.wire.clock import ClockMoveRequest
from tests.config_inputs import create_hub_app
from tests.flow_driver_world import (
    _GRAPH,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.flow_driver_world
    _REPO,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.flow_driver_world
    World,
    _HeldLanding,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.flow_driver_world
)
from tests.support import (
    TEST_HUB_ACTIONS,
    build_hub,
    capture_statements,
    chunk_stores,
    chunk_unit,
    default_tenant,
    emitted_events,
    hub_scoped,
    hub_store_connections,
    stamp,
)

pytestmark = pytest.mark.component


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


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _submitted_once(world: World, chunk_id: str) -> Submission:
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


def test_a_worker_transition_onto_the_node_is_submitted_once_and_takes_its_turn(world: World) -> None:
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


def test_controlled_step_reports_probe_failure_and_recovers(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = ControlledClock(world.hub.clock.now())
    driver = FlowDriver(world.stores, clock)
    app = create_hub_app(
        world.hub.wired_app.state.config,
        services=world.hub.hub_services,
        test_flow_router=controlled_flow_router(driver, clock, asyncio.Lock()),
    )
    probe = world.stores.corpus.flow_lines_signature
    monkeypatch.setattr(world.stores.corpus, "flow_lines_signature", Mock(side_effect=RuntimeError("probe down")))
    with TestClient(app) as client:
        failed = client.post("/api/admin/test-flow/step")
        assert failed.status_code == 500
        assert failed.json() == {"detail": "flow driver step failed"}
        monkeypatch.setattr(world.stores.corpus, "flow_lines_signature", probe)
        recovered = client.post("/api/admin/test-flow/step")
        assert recovered.status_code == 200
        assert recovered.json()["reconciled"] == 1


def test_controlled_step_completes_turn_and_move_does_not_run_or_publish(world: World) -> None:
    chunk_id = world.at_merge()
    clock = ControlledClock(world.hub.clock.now())
    driver = FlowDriver(world.stores, clock)
    lock = asyncio.Lock()
    app = create_hub_app(
        world.hub.wired_app.state.config,
        services=world.hub.hub_services,
        test_flow_router=controlled_flow_router(driver, clock, lock),
        controlled_clock_router=clock_router(clock, lock),
    )
    client = TestClient(app)
    step = client.post("/api/admin/test-flow/step")
    assert step.status_code == 200
    assert step.json()["started"] == 1
    submissions = world.submissions(chunk_id)
    assert len(submissions) == 1
    turns = client.get(f"/api/chunks/{chunk_id}/flow-submissions/{submissions[0].submission_id}/turns")
    assert turns.status_code == 200
    assert [(t["outcome"], t["ended_at"] is not None) for t in turns.json()["items"]] == [("pending", True)]
    assert world.action.passes == [chunk_id]

    # Consume the turn's wake, then a short move must not force another reconcile.
    assert client.post("/api/admin/test-flow/step").json()["reconciled"] == 1
    events = emitted_events(world.hub)
    facts = world.hub.services.flows.facts_of(submissions[0].submission_id)
    assert client.post("/api/admin/clock", json={"advance_seconds": 1}).status_code == 200
    assert emitted_events(world.hub) == events
    assert world.hub.services.flows.facts_of(submissions[0].submission_id) == facts
    assert world.action.passes == [chunk_id]
    assert client.post("/api/admin/test-flow/step").json()["skipped"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_controlled_step_holds_clock_until_real_turn_finishes(
    world: World, monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    chunk_id = world.at_merge()
    clock = ControlledClock(world.hub.clock.now())
    driver = FlowDriver(world.stores, clock)
    lock = asyncio.Lock()
    entered, release = threading.Event(), threading.Event()
    take = world.action.take

    async def held(visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        entered.set()
        assert await asyncio.to_thread(release.wait, 5)
        return await take(visit, forges, effects)

    monkeypatch.setattr(world.action, "take", held)
    step = next(r.endpoint for r in controlled_flow_router(driver, clock, lock).routes if isinstance(r, APIRoute))
    move = next(
        r.endpoint for r in clock_router(clock, lock).routes if isinstance(r, APIRoute) and r.methods == {"POST"}
    )
    initial = clock.now().isoformat()
    stepping = asyncio.create_task(step())
    assert await asyncio.to_thread(entered.wait, 5)
    if cancel:
        stepping.cancel()
    moving = asyncio.create_task(move(ClockMoveRequest(advance_seconds=1)))
    try:
        await asyncio.sleep(0)
        assert not stepping.done() and not moving.done()
        assert world.turn_outcomes(chunk_id) == [None]
    finally:
        release.set()
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await stepping
    else:
        assert (await stepping)["now"] == initial
    assert (await moving).previous == initial
    (submission,) = world.submissions(chunk_id)
    response = world.hub.client.get(f"/api/chunks/{chunk_id}/flow-submissions/{submission.submission_id}/turns")
    assert response.status_code == 200
    assert [(turn["outcome"], turn["ended_at"] is not None) for turn in response.json()["items"]] == [("pending", True)]


def test_a_hub_to_hub_transition_is_routed_by_a_turn_and_the_next_node_submitted_once(world: World) -> None:
    chunk_id = world.at_merge(_HUB_TO_HUB)
    assert world.detail(chunk_id)["current_node_name"] == "check"

    world.tick()
    assert world.detail(chunk_id)["current_node_name"] == "merge"
    (check,) = world.submissions(chunk_id)
    assert check.closed == "success" and check.flows == ()

    world.driver.wake()
    assert world.tick().submitted == 1
    assert [sub.closed for sub in world.submissions(chunk_id)] == ["success", None]


def test_a_restart_onto_the_node_is_submitted_once(world: World) -> None:
    chunk_id, _build = world.mint()
    world.hub.clock.advance(timedelta(seconds=1))
    resp = world.hub.client.post(f"/api/chunks/{chunk_id}/restart", json={"node": "merge"})
    assert resp.status_code == 202, resp.text
    assert world.detail(chunk_id)["current_node_name"] == "merge"

    _submitted_once(world, chunk_id)


def test_a_cross_graph_migration_onto_the_node_is_submitted_once(world: World) -> None:
    assert world.hub.client.post("/api/graphs", json={"definition_yaml": _LANDING}).status_code == 201
    chunk_id, build = world.mint(_GRAPH.replace("          to: merge\n", "          to: graph:landing\n", 1))
    world.complete(chunk_id, build)
    detail = world.detail(chunk_id)
    assert (detail["status"], detail["current_node_name"]) == ("delivering", "merge")

    _submitted_once(world, chunk_id)


def test_a_chunk_paused_at_the_node_takes_no_turn_until_it_is_resumed(world: World) -> None:
    chunk_id, build = world.mint()
    assert world.hub.client.post(f"/api/chunks/{chunk_id}/pause", json={}).status_code == 202
    world.complete(chunk_id, build)
    assert world.detail(chunk_id)["status"] == "paused"
    assert (world.tick().submitted, world.action.passes) == (0, [])

    assert world.hub.client.post(f"/api/chunks/{chunk_id}/resume", json={}).status_code == 202

    _submitted_once(world, chunk_id)
    assert world.action.passes == [chunk_id]


def test_a_requeue_after_an_escalation_is_a_new_visit_at_the_back(world: World) -> None:
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


def test_two_chunks_on_one_branch_take_turns_in_arrival_order_and_a_pending_one_yields(world: World) -> None:
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


def test_a_chunk_that_lands_closes_with_its_verdict(world: World) -> None:
    chunk_id = world.at_merge()
    world.action.answer = Pass(choice="landed")

    world.tick()

    assert world.detail(chunk_id)["status"] == "done"
    (sub,) = world.submissions(chunk_id)
    assert sub.closed == "landed"
    facts = [(f.kind, f.outcome) for f in world.hub.services.flows.facts_of(sub.submission_id)]
    assert facts == [("submitted", None), ("turn-started", None), ("turn-ended", "landed"), ("withdrawn", "landed")]


def test_hub_advance_wakes_the_driver_and_answers_where_the_visit_stands(world: World) -> None:
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


def test_a_pending_visit_is_reported_waiting_on_its_poll(world: World) -> None:
    chunk_id = world.at_merge()
    world.tick()

    detail = world.hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance").json()["detail"]

    assert detail.startswith("pending — next poll at ")


# --- nothing left holding a place ---------------------------------------------------------------------------------


def test_a_stopped_chunk_closes_stopped(world: World) -> None:
    chunk_id = world.at_merge()
    _submitted_once(world, chunk_id)

    assert world.hub.client.post(f"/api/chunks/{chunk_id}/stop", json={}).status_code in (200, 202)
    world.tick()

    assert world.submissions(chunk_id)[0].closed == "stopped"


def test_a_chunk_held_by_its_person_at_a_later_epoch_stays_open_and_takes_no_turn(world: World) -> None:
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


def test_a_visit_that_cannot_name_its_resources_is_refused_at_submit_once(world: World) -> None:
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


#: ``merge``'s failure edge, as authored.
_MERGE_FAILURE = "          description: Failed to land.\n          to: build\n"
#: Each failure edge a refusal routes, and the routing write it takes: a node edge transitions, a cross-graph edge
#: migrates, and an edge to a node the graph lacks escalates.
_FAILURE_ROUTES: dict[str, tuple[type, str]] = {
    "node": (ChunkHubExecStore, "record_hub_step_transition_locked"),
    "cross-graph": (ChunkMovementStore, "record_migration_locked"),
    "unroutable": (ChunkEscalationsStore, "record_bounce_escalation_locked"),
}
#: The events a refusal along each failure edge records, once.
_REFUSAL_EVENTS = {
    "node": ["repository-unresolved"],
    "cross-graph": ["repository-unresolved"],
    "unroutable": ["repository-unresolved", "hub-node-unroutable-outcome"],
}
_REFUSAL_KINDS = frozenset(_REFUSAL_EVENTS["unroutable"])


@dataclass(frozen=True)
class _Refusal:
    """What a refusal left of the chunk: where it stands, its ``hub-`` artifacts, the kinds of its event rows, and
    the kinds of the ``event-logged`` frames published for it."""

    standing: tuple[str, str, str]
    artifacts: list[str]
    events: list[str]
    frames: list[str]


def _refusal(world: World, chunk_id: str, *, since: int) -> _Refusal:
    detail = world.detail(chunk_id)
    artifacts = world.hub.services.chunks.artifacts.load_artifacts(chunk_id)
    with world.hub.engine.connect() as conn:
        events = conn.execute(
            hub_scoped(select(s.event_log.c.kind).where(s.event_log.c.chunk_id == chunk_id).order_by(s.event_log.c.id))
        ).scalars()
        kinds = [kind for kind in events if kind in _REFUSAL_KINDS]
    frames = [json.loads(e["data"]) for e in emitted_events(world.hub, since=since)]
    return _Refusal(
        standing=(detail["graph_id"], detail["current_node_name"], detail["status"]),
        artifacts=sorted(a.name for a in artifacts if a.name.startswith("hub-")),
        events=kinds,
        frames=[f["kind"] for f in frames if f.get("chunk_id") == chunk_id and f.get("kind") in _REFUSAL_KINDS],
    )


def _routing_failure_to(world: World, route: str, *, repo: str) -> str:
    """A chunk standing at ``merge`` whose visit names ``repo``, ``merge``'s failure edge routing as ``route``
    names."""
    graph = _GRAPH
    if route == "cross-graph":
        assert world.hub.client.post("/api/graphs", json={"definition_yaml": _LANDING}).status_code == 201
        graph = _GRAPH.replace(_MERGE_FAILURE, _MERGE_FAILURE.replace("to: build", "to: graph:landing"))
    chunk_id, build = world.mint(graph)
    world.complete(chunk_id, build, repo=repo)
    if route == "unroutable":
        graph_id = world.detail(chunk_id)["graph_id"]
        with world.hub.engine.begin() as conn:  # as a graph naming a node it lacks would route
            merge = conn.execute(
                hub_scoped(
                    select(s.graph_nodes.c.node_id).where(
                        s.graph_nodes.c.graph_id == graph_id, s.graph_nodes.c.name == "merge"
                    )
                )
            ).scalar_one()
            conn.execute(
                hub_scoped(
                    update(s.graph_edges)
                    .where(s.graph_edges.c.from_node_id == merge, s.graph_edges.c.to_node_name == "build")
                    .values(to_node_name="nowhere")
                )
            )
    return chunk_id


def _refused_at_merge(world: World, route: str) -> str:
    """A chunk standing at ``merge`` whose visit names a repository no record holds, so the reconcile refuses it,
    routing ``merge``'s failure edge as ``route`` names."""
    return _routing_failure_to(world, route, repo="acme/unknown")


@pytest.mark.parametrize("raises_at", ["routing", "event"])
@pytest.mark.parametrize("route", list(_FAILURE_ROUTES))
def test_a_refusal_that_raises_part_way_commits_nothing_and_a_later_pass_records_it_once(
    world: World, monkeypatch: pytest.MonkeyPatch, route: str, raises_at: str
) -> None:
    chunk_id = _refused_at_merge(world, route)
    at_merge = _refusal(world, chunk_id, since=0).standing
    since = world.hub.events.latest_id()
    raised: list[str] = []
    store, method = _FAILURE_ROUTES[route]
    original = getattr(store, method)
    record_event = ChunkEventsStore.record_event

    def route_then_raise(self: object, *args: Any, **kwargs: Any) -> Any:
        original(self, *args, **kwargs)
        raised.append(method)
        raise RuntimeError("the routing write failed after it ran")

    def raise_instead(self: ChunkEventsStore, **kwargs: Any) -> int:
        if kwargs["kind"] == "repository-unresolved":
            raised.append("record_event")
            raise RuntimeError("the event write failed")
        return record_event(self, **kwargs)

    with monkeypatch.context() as patch:
        if raises_at == "routing":
            patch.setattr(store, method, route_then_raise)
        else:
            patch.setattr(ChunkEventsStore, "record_event", raise_instead)
        world.lines(world.tenant).reconcile(0)
    failed = _refusal(world, chunk_id, since=since)

    committed: list[bool] = []
    publish_event_logged = EventBroker.publish_event_logged

    def publish_once_committed(self: EventBroker, **frame: Any) -> int:
        row_id = int(frame["key"].removeprefix("event_log:"))
        with world.hub.engine.connect() as conn:  # a fresh connection sees only what has committed
            row = conn.execute(hub_scoped(select(s.event_log.c.id).where(s.event_log.c.id == row_id))).first()
        committed.append(row is not None)
        return publish_event_logged(self, **frame)

    monkeypatch.setattr(EventBroker, "publish_event_logged", publish_once_committed)
    world.lines(world.tenant).reconcile(0)
    retried = _refusal(world, chunk_id, since=since)

    assert raised == [method if raises_at == "routing" else "record_event"]
    graph_id = at_merge[0]
    landing = world.hub.services.graphs.get_enabled_by_name("landing")
    routed = {
        "node": (graph_id, "build", "running"),
        "cross-graph": (landing.graph_id if landing is not None else "", "merge", "delivering"),
        "unroutable": (graph_id, "merge", "needs_human"),
    }[route]
    hub_logs = ["hub-log.repository-unresolved"] + (["hub-unroutable-outcome"] if route == "unroutable" else [])
    assert (failed, retried) == (
        _Refusal(standing=at_merge, artifacts=[], events=[], frames=[]),
        _Refusal(standing=routed, artifacts=hub_logs, events=_REFUSAL_EVENTS[route], frames=_REFUSAL_EVENTS[route]),
    )
    assert committed and all(committed), "every event-logged frame was published after its row committed"


@pytest.mark.parametrize(("lever", "body"), [("stop", {"by": "operator"}), ("restart", {"node": "merge"})])
def test_a_refusal_whose_chunk_left_the_visit_before_its_transaction_writes_nothing(
    world: World, monkeypatch: pytest.MonkeyPatch, lever: str, body: dict[str, str]
) -> None:
    chunk_id = _refused_at_merge(world, "node")
    since = world.hub.events.latest_id()
    refuse = HubNodeRouter.refuse
    outcomes: list[object] = []

    def lever_then_refuse(self: HubNodeRouter, *args: Any, **kwargs: Any) -> None:
        world.hub.clock.advance(timedelta(seconds=1))
        outcomes.append(world.hub.client.post(f"/api/chunks/{chunk_id}/{lever}", json=body).status_code)
        try:
            outcomes.append(refuse(self, *args, **kwargs))
        except Exception as exc:
            outcomes.append(exc)
            raise

    monkeypatch.setattr(HubNodeRouter, "refuse", lever_then_refuse)
    world.lines(world.tenant).reconcile(0)

    assert outcomes == [202, None], "the refusal wrote nothing and raised nothing"
    left = _refusal(world, chunk_id, since=since)
    assert left.standing[1:] == ("merge", "stopped" if lever == "stop" else "delivering")
    assert (left.artifacts, left.events, left.frames) == ([], [], [])


#: The outcome a turn routing ``merge``'s failure edge ends on.
_TURN_OUTCOME = {"node": "failure", "cross-graph": "failure", "unroutable": "escalated"}


def _refuse_at_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """The turn's own resolution refuses its visit, as one whose repository records went away after it was
    submitted: it resolves against no records."""

    def unresolved(artifacts: Any, **kwargs: Any) -> Any:
        kwargs["repositories"] = []
        return resolve_visit(artifacts, **kwargs)

    monkeypatch.setattr(flow_turn, "resolve_visit", unresolved)


@pytest.mark.parametrize("route", list(_FAILURE_ROUTES))
def test_a_turns_refusal_whose_end_fails_commits_and_publishes_none_of_it(
    world: World, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    chunk_id = _routing_failure_to(world, route, repo=_REPO)
    at_merge = _refusal(world, chunk_id, since=0).standing
    since = world.hub.events.latest_id()
    _refuse_at_the_turn(monkeypatch)
    end_turn = FlowStore.end_turn_locked
    raised: list[str] = []

    def end_then_raise(self: FlowStore, *args: Any, **kwargs: Any) -> Any:
        end_turn(self, *args, **kwargs)
        raised.append(kwargs["outcome"])
        raise TurnEndRefused("the turn's end failed after it ran")

    monkeypatch.setattr(FlowStore, "end_turn_locked", end_then_raise)
    world.tick()

    assert raised == [_TURN_OUTCOME[route]]
    assert _refusal(world, chunk_id, since=since) == _Refusal(standing=at_merge, artifacts=[], events=[], frames=[])
    assert world.turn_outcomes(chunk_id) == [None], "the turn is still open"


#: Each lever that takes a chunk off its visit mid-turn: its body, the chunk's status after it, and the closure the
#: turn's end gives the submission.
_TURN_LEVERS: dict[str, tuple[dict[str, str], str, Withdrawal | None]] = {
    "stop": ({"by": "operator"}, "stopped", "stopped"),
    "restart": ({"node": "merge"}, "delivering", "restarted"),
}


@pytest.mark.parametrize("lever", list(_TURN_LEVERS))
@pytest.mark.parametrize(
    ("answered", "route"), [("refused", "node"), ("refused", "unroutable"), ("routed", "unroutable")]
)
def test_a_turn_whose_chunk_left_the_visit_before_its_routing_writes_nothing_but_its_end(
    world: World, monkeypatch: pytest.MonkeyPatch, answered: str, route: str, lever: str
) -> None:
    chunk_id = _routing_failure_to(world, route, repo=_REPO)
    graph_id = world.detail(chunk_id)["graph_id"]
    since = world.hub.events.latest_id()
    body, status, closure = _TURN_LEVERS[lever]
    if answered == "refused":
        _refuse_at_the_turn(monkeypatch)
    else:
        world.action.answer = Pass(choice="failure")
    method = "refuse" if answered == "refused" else "route"
    original = getattr(HubNodeRouter, method)
    outcomes: list[object] = []

    def lever_then_route(self: HubNodeRouter, *args: Any, **kwargs: Any) -> None:
        outcomes.append(world.hub.client.post(f"/api/chunks/{chunk_id}/{lever}", json=body).status_code)
        try:
            outcomes.append(original(self, *args, **kwargs))
        except Exception as exc:
            outcomes.append(exc)
            raise

    monkeypatch.setattr(HubNodeRouter, method, lever_then_route)
    world.tick()

    assert outcomes == [202, None], "the routing wrote nothing and raised nothing"
    assert _refusal(world, chunk_id, since=since) == _Refusal(
        standing=(graph_id, "merge", status), artifacts=[], events=[], frames=[]
    )
    assert world.turn_outcomes(chunk_id) == [_TURN_OUTCOME[route]]
    assert [sub.closed for sub in world.submissions(chunk_id)] == [closure]


def test_a_chunk_at_an_actionless_node_is_escalated_by_the_reconcile_one_epoch_up(world: World) -> None:
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


def test_an_unchanged_signature_skips_the_rescan_until_the_floor(world: World) -> None:
    world.at_merge()
    assert world.tick().reconciled == (world.tenant,)
    world.tick()  # the wake its turn's end sent
    # A submission no chunk stands behind, written where the probe does not look: only a full rescan closes it.
    with chunk_unit(hub_store_connections(world.hub.engine), "ch_ghost") as unit:
        ghost = FlowStore(hub_store_connections(world.hub.engine), world.hub.clock).submit(
            "ch_ghost",
            node_id="nd_gone",
            epoch=1,
            generation=0,
            resources=[],
            at=world.hub.clock.now(),
            transaction=unit,
        )
    assert ghost is not None

    world.hub.clock.advance(RECONCILE_FLOOR - timedelta(seconds=1))
    skipped = world.tick()
    assert (skipped.skipped, skipped.closed) == ((world.tenant,), 0)

    world.hub.clock.advance(timedelta(seconds=1))
    floor = world.tick()
    assert (floor.reconciled, floor.closed) == ((world.tenant,), 1)


# --- a turn no task answers for -----------------------------------------------------------------------------------


def test_a_turn_a_tick_started_but_no_task_ran_is_ended_by_the_sweep_within_two_ticks(world: World) -> None:
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


def test_a_chunk_a_person_takes_after_its_turn_is_taken_gets_no_pass(world: World) -> None:
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


def test_a_running_turn_is_never_swept(world: World) -> None:
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


def test_boot_ends_a_planted_open_turn_interrupted_once(world: World) -> None:
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


def test_the_first_boot_submits_chunks_mid_delivery_in_the_order_they_entered_their_node(world: World) -> None:
    later, build_later = world.mint(ref="later")
    earlier, build_earlier = world.mint(ref="earlier")
    world.complete(earlier, build_earlier)
    world.hub.clock.advance(timedelta(seconds=1))
    world.complete(later, build_later)

    world.driver.recover_turns(world.tenant)

    (first,), (second,) = world.submissions(earlier), world.submissions(later)
    assert first.arrival < second.arrival


# --- forge budget notices -----------------------------------------------------------------------------------------


def _detail(raw: object) -> dict[str, object]:
    # The column decodes to a dict on some dialects and stays a JSON string on others.
    return json.loads(raw if isinstance(raw, str) else json.dumps(raw))


def _budget_events(world: World) -> list[tuple[str, str, dict[str, object]]]:
    with world.hub.engine.connect() as conn:
        rows = conn.execute(
            hub_scoped(
                select(s.event_log.c.kind, s.event_log.c.severity, s.event_log.c.detail)
                .where(s.event_log.c.kind.in_(("forge-budget-low", "forge-rate-limited")))
                .order_by(s.event_log.c.id)
            )
        ).all()
    return [(r.kind, r.severity, _detail(r.detail)) for r in rows]


def test_the_tick_drains_each_budget_notice_once_per_window_into_the_tenants_log(world: World) -> None:
    budgets = world.hub.open_services(world.tenant).forge_budgets
    key = BudgetKey("github", "token")
    reset = world.hub.clock.now() + timedelta(minutes=2)
    headers = {
        "X-RateLimit-Limit": "100",
        "X-RateLimit-Remaining": "30",
        "X-RateLimit-Reset": str(int(reset.timestamp())),
    }
    reading = rate_dialect("github").reading(headers, world.hub.clock.now())
    assert reading is not None
    budgets.learn(key, reading)

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


def test_a_new_window_drains_its_notice_again(world: World) -> None:
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
    budgets = ForgeBudgets(clock=clock, dialects=rate_dialect)
    key = BudgetKey("github", None)
    budgets.spent(key, clock.now() + timedelta(minutes=1))
    budgets.learn(key, BudgetReading("core", 100, 10, clock.now() + timedelta(minutes=1)))
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
    world: World, tmp_path: Path
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


def test_a_raising_tenant_does_not_stop_the_others(world: World) -> None:
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


def test_a_failing_tick_skips_its_cycle_and_never_ends_run(world: World) -> None:
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
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken, build_broken = world.mint(ref="broken")
    sound, build_sound = world.mint(ref="sound")
    world.complete(broken, build_broken)
    world.complete(sound, build_sound)
    submit = FlowLineReconciler.submit

    def flaky(self: FlowLineReconciler, standing: _Standing, artifacts: Sequence[StoredArtifact]) -> Submission | None:
        if standing.chunk.chunk_id == broken:
            raise RuntimeError("unreadable visit")
        return submit(self, standing, artifacts)

    monkeypatch.setattr(FlowLineReconciler, "submit", flaky)

    result = world.tick()

    assert result.failed == () and result.reconciled == (world.tenant,)
    assert world.submissions(broken) == [] and len(world.submissions(sound)) == 1


def _artifact_reads(world: World) -> int:
    with capture_statements(world.hub.engine) as statements:
        world.tick()
    return sum(1 for statement, _ in statements if " FROM artifacts" in statement)


def test_a_reconcile_pass_reads_artifacts_in_a_constant_number_of_statements(tmp_path: Path) -> None:
    reads: list[int] = []
    for n in (1, 3):
        (tmp_path / str(n)).mkdir()
        world = World(tmp_path / str(n))
        for i in range(n):
            chunk, build = world.mint(ref=f"r{i}")
            world.complete(chunk, build)
        reads.append(_artifact_reads(world))
    assert reads[0] == reads[1]
