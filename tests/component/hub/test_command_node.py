"""The generic hub command node primitive (#65) — unit + component tiers.

Unit tier: graph validation plus the pure poll-policy and env-injection helpers. Component
tier: the executor taking actions' passes over a real hub store, including the
serialization barrier every parked chunk must honor.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest

from blizzard.hub.delivery.action_registry import HubActions
from blizzard.hub.delivery.actions.base import IHubAction
from blizzard.hub.domain.actions.effects import IEffectSink, Pass
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.domain.chunk.model import (
    WorkRef,
    decide_close_enqueue,
)
from blizzard.hub.domain.chunk.ports.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.graph.actions import ActionSpec
from blizzard.hub.domain.graph.model import Graph
from blizzard.hub.forge.landing import VisitForges
from tests.support.hub_command_graphs import (
    _POINTER,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.hub_command_graphs
    _POLL_GRAPH_YAML,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.hub_command_graphs
    ALWAYS_PENDING_GRAPH_YAML,
    HUB_CMD_GRAPH_YAML,
    post_build_pass,
    submit_build_pass,
    to_merge_node,
)
from tests.support.hub_node_artifacts import record_hub_asset
from tests.support.scaffolding import (
    TEST_HUB_ACTIONS,
    FakeWorkSource,
    HubHarness,
    build_hub,
    chunk_stores,
    pointer_token,
    report_lease,
)


def _answering(choice: str) -> str:
    """:data:`HUB_CMD_GRAPH_YAML` with ``merge`` answering ``choice``."""
    return HUB_CMD_GRAPH_YAML.replace("with: {choice: [success]}", f"with: {{choice: [{choice}]}}")


# Unit — validation


# Unit — pending outcome (#66)


_BUILD_TO_MERGE = {
    "executor": "runner",
    "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "merge"}}},
}


# Unit — env injection


def test_a_restart_mid_hub_node_run_fences_out_a_stale_marker_write(tmp_path: Path) -> None:
    """A restart during a visit fences a stale merged marker and its close intents (``bzh:epoch-fencing``)."""
    hub = build_hub(tmp_path)
    chunk_id, _build_node_id, graph = to_merge_node(hub)
    merge_node = graph.node_by_name("merge")
    assert merge_node is not None
    before = hub.clock.now()

    assert hub.client.post(f"/api/chunks/{chunk_id}/restart", json={}).status_code == 202

    recorded = record_hub_asset(
        hub,
        chunk_id,
        node_id=merge_node.node_id,
        node_name="merge",
        epoch=1,  # the run's own epoch, read before the restart moved the chunk on
        name="merged/acme-widget",
        content="sha:abc123",
    )

    assert recorded is False
    names = {a.name for a in hub.services.chunks.artifacts.load_artifacts(chunk_id)}
    assert "merged/acme-widget" not in names
    assert hub.services.chunks.delivery.count_landed_since("acme-widget", before) == 0


def test_a_blank_landing_marker_is_stored_but_owes_no_close_intent(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _build_node_id, graph = to_merge_node(hub)
    merge_node = graph.node_by_name("merge")
    assert merge_node is not None

    def intents() -> list[int]:
        return [i.intent_id for i in hub.services.chunks.delivery.pending_close_intents() if i.chunk_id == chunk_id]

    intents_before = intents()

    def record(name: str, content: str) -> bool:
        return record_hub_asset(
            hub, chunk_id, node_id=merge_node.node_id, node_name="merge", epoch=1, name=name, content=content
        )

    assert record("merged/acme-blank", "  ") is True
    names = {a.name for a in hub.services.chunks.artifacts.load_artifacts(chunk_id)}
    assert "merged/acme-blank" in names
    assert intents() == intents_before
    assert not [
        e for e in hub.services.chunks.events.list_events(chunk_id=chunk_id) if e.kind == "work-item-close-skipped"
    ]

    assert record("merged/acme-widget", "sha:abc123") is True
    assert intents() == intents_before
    assert (
        len(
            [
                e
                for e in hub.services.chunks.events.list_events(chunk_id=chunk_id)
                if e.kind == "work-item-close-skipped"
            ]
        )
        == 1
    )


def test_a_stopped_chunk_fences_out_a_still_running_marker_write(tmp_path: Path) -> None:
    """Stop mints no epoch: terminality must fence the still-current run's marker
    (``bzh:epoch-fencing``), landing and close intent."""
    hub = build_hub(tmp_path)
    chunk_id, _build_node_id, graph = to_merge_node(hub)
    merge_node = graph.node_by_name("merge")
    assert merge_node is not None
    before = hub.clock.now()

    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"}).status_code == 202

    recorded = record_hub_asset(
        hub,
        chunk_id,
        node_id=merge_node.node_id,
        node_name="merge",
        epoch=1,  # the run's own epoch — a stop mints no fresher one to fence it out
        name="merged/acme-widget",
        content="sha:abc123",
    )

    assert recorded is False
    names = {a.name for a in hub.services.chunks.artifacts.load_artifacts(chunk_id)}
    assert "merged/acme-widget" not in names
    assert hub.services.chunks.delivery.count_landed_since("acme-widget", before) == 0
    assert [i for i in hub.services.chunks.delivery.pending_close_intents() if i.chunk_id == chunk_id] == []


def test_a_stopped_chunk_fences_out_a_still_running_step_transition(tmp_path: Path) -> None:
    """``record_hub_step_transition`` mirrors ``record_hub_artifact``'s guards (#65) — the
    sibling of the marker-write case above: a stop landing mid-visit must fence the
    node's own exit transition too, not just its marker."""
    hub = build_hub(tmp_path)
    chunk_id, _build_node_id, graph = to_merge_node(hub)
    merge_node = graph.node_by_name("merge")
    assert merge_node is not None

    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"}).status_code == 202

    stores = chunk_stores(hub.engine, hub.clock)
    with stores.exclusive.locked([chunk_id]) as handle:
        recorded = stores.hub_exec.record_hub_step_transition_locked(
            handle,
            chunk_id,
            from_node_id=merge_node.node_id,
            to_node_id="done",
            choice_name="success",
            epoch=1,  # the run's own epoch — a stop mints no fresher one to fence it out
            runner_id="r1",
            transition_id="tr_stale",
            close_decision=decide_close_enqueue([WorkRef(**_POINTER)], lambda _: True),
            at=hub.clock.now(),
            artifacts=[],
            release_route=True,
            admission=EpochAdmission.ABOVE,
        )

    assert recorded is False
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "stopped"
    assert all(t["from_node_name"] != "merge" for t in detail["history"])


class _Gate:
    """An in-process action holding each pass until released, counting the passes it took and how many ran at
    once."""

    spec = ActionSpec(
        name="test.gate",
        args=frozenset(),
        required_args=frozenset(),
        answers=frozenset({"success"}),
        answers_from_arg=None,
        lands=False,
    )

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()
        self.passes = 0
        self.in_flight = 0
        self.max_in_flight = 0

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        with self._lock:
            self.passes += 1
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.entered.set()
        self.release.wait(timeout=5)
        with self._lock:
            self.in_flight -= 1
        return Pass(choice="success")


class _Recorded:
    """An in-process action answering ``pending`` on its first pass and ``success`` after, keeping every visit
    it was handed."""

    spec = ActionSpec(
        name="test.recorded",
        args=frozenset(),
        required_args=frozenset(),
        answers=frozenset({"pending", "success"}),
        answers_from_arg=None,
        lands=False,
    )

    def __init__(self) -> None:
        self.visits: list[Visit] = []

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        self.visits.append(visit)
        return Pass(choice="pending" if len(self.visits) == 1 else "success")


def _hub_with(
    tmp_path: Path,
    *extra: IHubAction,
    work_sources: dict[str, FakeWorkSource] | None = None,
    public_url: str | None = None,
) -> HubHarness:
    actions = HubActions.of([*TEST_HUB_ACTIONS.implementations.values(), *extra])
    return build_hub(tmp_path, actions=actions, work_sources=work_sources, public_url=public_url)


def _gated(choice_action: str = "test.gate") -> str:
    return HUB_CMD_GRAPH_YAML.replace("action: test.answer\n    with: {choice: [success]}", f"action: {choice_action}")


def _passes(hub: HubHarness, chunk_id: str) -> int:
    """How many ``test.sequence`` passes the chunk's node took — one marker each."""
    names = [a.name for a in hub.services.chunks.artifacts.load_artifacts(chunk_id)]
    return sum(1 for name in names if name.startswith("test-sequence/"))


def test_a_pass_maps_its_answer_to_the_authored_edge(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, _graph = to_merge_node(hub)

    apply = submit_build_pass(hub, chunk_id, build_node_id, 1)
    assert apply.json()["outcome"] == "hub_node_taken"

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "done"
    land_transitions = [t for t in detail["history"] if t["from_node_name"] == "merge"]
    assert len(land_transitions) == 1
    assert land_transitions[0]["choice_name"] == "success"
    assert land_transitions[0]["to_node_id"] == "done"  # the reserved terminal — no node to name


def test_a_hub_node_landing_done_releases_the_route(tmp_path: Path) -> None:
    """The hub node's ``done`` is the release a runner-authored ``done`` never writes."""
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, _graph = to_merge_node(hub)

    assert submit_build_pass(hub, chunk_id, build_node_id, 1).json()["outcome"] == "hub_node_taken"

    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"
    assert hub.services.chunks.route.route_of(chunk_id) is None


def test_a_failed_work_item_fetch_degrades_the_feature_title_to_absent(tmp_path: Path) -> None:
    """A forge read failing while resolving the feature title must never break delivery
    : the turn still takes its pass, its visit naming no title."""
    recorded = _Recorded()
    hub = _hub_with(tmp_path, recorded, work_sources={"default": FakeWorkSource(fail_refs={"42"})})
    chunk_id, build_node_id, _graph = to_merge_node(hub, graph_yaml=_gated("test.recorded"))

    assert submit_build_pass(hub, chunk_id, build_node_id, 1).json()["outcome"] == "hub_node_taken"

    assert [visit.feature_title for visit in recorded.visits] == [None]


def test_a_failure_answer_maps_to_the_failure_edge(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, _graph = to_merge_node(hub, graph_yaml=_answering("failure"))

    apply = submit_build_pass(hub, chunk_id, build_node_id, 1)
    assert apply.json()["outcome"] == "hub_node_taken"

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    # Routed back to `build` (the `failure` edge's target) — running again, not done.
    assert detail["status"] == "running"
    assert detail["current_node_name"] == "build"


def _submit_build_pass_with_commit(hub: HubHarness, chunk_id: str, build_node_id: str, epoch: int, *, repo: str):
    """Like :func:`submit_build_pass`, but carrying one ``git_commit`` artifact for
    ``repo`` — the shape a hub node's kick-back accounting reads to tell a genuine
    delivery attempt from a bare hub-command smoke test."""
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": epoch,
            "runner_id": "r1",
            "from_node_id": build_node_id,
            "check_results": [],
            "artifacts": [
                {
                    "name": "w",
                    "kind": "git_commit",
                    "repo": repo,
                    "branch_name": "b",
                    "commit_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                }
            ],
        },
    )
    hub.settle_turns()
    return resp


def test_a_non_terminal_route_with_nothing_landed_records_a_bounce(tmp_path: Path) -> None:
    """A non-terminal route with nothing landed is a delivery kick-back (#64) — by
    the domain fact (no ``merged/<repo>`` marker), never by choice name."""
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, _graph = to_merge_node(hub, graph_yaml=_answering("failure"))

    apply = _submit_build_pass_with_commit(hub, chunk_id, build_node_id, 1, repo="acme/widget")
    assert apply.json()["outcome"] == "hub_node_taken"

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["current_node_name"] == "build"
    assert len(detail["bounces"]) == 1
    assert detail["bounces"][0]["cause"] == "failure"
    bounce_assets = [a for a in detail["artifacts"] if a["name"] == "bounce-envelope"]
    assert len(bounce_assets) == 1, detail["artifacts"]
    assert detail["landed"] is False


def test_a_fully_landed_non_terminal_route_records_no_bounce(tmp_path: Path) -> None:
    """The counterpart: every repo already carries its ``merged/<repo>`` marker, so a
    non-terminal continuation (an authored ``landed -> <node>`` edge, #63) is forward
    progress, never a kick-back — no bounce fact, whatever the outcome's name."""
    hub = build_hub(tmp_path)
    graph_yaml = """
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
    action: test.answer
    with: {choice: [landed]}
    judgement:
      choices:
        landed:
          description: Landed; continue to verify.
          to: verify
        failure:
          description: Failed to land.
          to: build
  verify:
    executor: runner
    prompt: |
      Confirm.
    judgement:
      prompt: |
        Assess the landed change.
      choices:
        pass:
          description: Confirmed.
          to: done
"""
    chunk_id, build_node_id, graph = to_merge_node(hub, graph_yaml=graph_yaml)
    merge_node = graph.node_by_name("merge")
    assert merge_node is not None
    # The repo already landed — pre-record the marker as an earlier pass would have.
    cast(IWriteChunkArtifactsRepository, hub.services.chunks.artifacts).record_hub_artifact(
        chunk_id,
        node_id=merge_node.node_id,
        node_name="merge",
        epoch=1,
        name="merged/acme/widget",
        close_decision=decide_close_enqueue([WorkRef(**_POINTER)], lambda _: True),
        content="c",
        at=hub.clock.now(),
        admission=EpochAdmission.AT_OR_ABOVE,
    )

    apply = _submit_build_pass_with_commit(hub, chunk_id, build_node_id, 1, repo="acme/widget")
    assert apply.json()["outcome"] == "hub_node_taken"

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["current_node_name"] == "verify"
    assert detail["bounces"] == []
    assert detail["landed"] is True


def test_hub_advance_endpoint_no_ops_off_a_non_hub_command_node(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": HUB_CMD_GRAPH_YAML}).status_code == 201
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]})
    chunk_id = resp.json()["chunk_id"]
    result = hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")
    assert result.status_code == 200
    assert result.json()["ran"] is False


def test_a_chunks_own_live_turn_is_never_reentered_and_a_finished_one_never_replays(tmp_path: Path) -> None:
    """While a chunk's hub node takes its turn, another tick takes none for that chunk; once the turn ended, a
    replayed completion and further ticks take none either."""
    gate = _Gate()
    hub = _hub_with(tmp_path, gate)
    chunk_id, build_node_id, _graph = to_merge_node(hub, graph_yaml=_gated())
    assert post_build_pass(hub, chunk_id, build_node_id, 1).json()["outcome"] == "hub_node_taken"

    thread = threading.Thread(target=hub.take_turns)
    thread.start()
    assert gate.entered.wait(timeout=5), "the hub node never started"

    (during,) = hub.take_turns()
    assert [t for t in during.started if t.submission.chunk_id == chunk_id] == []
    assert gate.passes == 1

    gate.release.set()
    thread.join(timeout=5)

    assert submit_build_pass(hub, chunk_id, build_node_id, 1).json()["outcome"] == "hub_node_taken"
    assert gate.passes == 1
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert len([t for t in detail["history"] if t["from_node_name"] == "merge"]) == 1


# Component — pending outcome (#66): polls without blocking the queue


def _to_poll_merge_node(
    hub: HubHarness, pointer: Mapping[str, str], graph_yaml: str = _POLL_GRAPH_YAML
) -> tuple[str, str, Graph]:
    """Same shape as ``to_merge_node`` but minting a pending-capable poll graph."""
    return to_merge_node(hub, pointer=pointer, graph_yaml=graph_yaml)


def test_pending_records_no_transition_and_ends_the_turn(tmp_path: Path) -> None:
    """A ``pending`` answer parks the chunk without a transition, and the fleet-wide
    turn ends with it — the property a pending node must hold."""
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, _graph = _to_poll_merge_node(hub, {"source": "default", "ref": "poll-1"})

    apply = submit_build_pass(hub, chunk_id, build_node_id, 1)
    assert apply.json()["outcome"] == "hub_node_taken"

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "delivering"
    assert detail["current_node_name"] == "merge"
    assert detail["pending"] is not None
    assert detail["pending"]["node_name"] == "merge"
    # No bounce recorded — pending itself is not contention (#64's kick-back path).
    assert detail["bounces"] == []


def test_hub_advance_respects_the_poll_interval_then_succeeds(tmp_path: Path) -> None:
    """Before ``poll_interval`` elapses the driver takes no turn; once elapsed, the node takes its next pass and
    a ``success`` routes ``done``."""
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, _graph = _to_poll_merge_node(hub, {"source": "default", "ref": "poll-2"})

    apply = submit_build_pass(hub, chunk_id, build_node_id, 1)
    assert apply.json()["outcome"] == "hub_node_taken"
    assert _passes(hub, chunk_id) == 1

    # Not yet due (poll_interval: 30) — the driver takes no turn.
    assert hub.turn_outcome(chunk_id) is None
    assert _passes(hub, chunk_id) == 1

    hub.clock.advance(timedelta(seconds=31))
    assert hub.turn_outcome(chunk_id) == "success"
    assert _passes(hub, chunk_id) == 2

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "done"
    assert detail["pending"] is None


def test_a_slug_renamed_between_two_passes_of_one_chunk_reaches_the_second_under_the_same_id(
    tmp_path: Path,
) -> None:
    """Each pass reads its chunk's project as it starts: a re-poll after a rename names the new slug."""
    recorded = _Recorded()
    hub = _hub_with(tmp_path, recorded)
    project_id = hub.client.get("/api/projects/default").json()["project_id"]
    graph_yaml = _POLL_GRAPH_YAML.replace(
        "action: test.sequence\n    with: {choices: [pending, success]}", "action: test.recorded"
    )
    chunk_id, build_node_id, _graph = _to_poll_merge_node(
        hub, {"source": "default", "ref": "poll-rename"}, graph_yaml=graph_yaml
    )
    assert submit_build_pass(hub, chunk_id, build_node_id, 1).json()["outcome"] == "hub_node_taken"

    renamed = hub.client.patch("/api/projects/default", json={"slug": "widgets"})
    assert renamed.status_code == 200, renamed.text
    hub.clock.advance(timedelta(seconds=31))
    assert hub.turn_outcome(chunk_id) == "success"

    assert [(visit.project.project_id, visit.project.slug) for visit in recorded.visits] == [
        (project_id, "default"),
        (project_id, "widgets"),
    ]


def test_pending_chunk_does_not_block_another_chunks_hub_node(tmp_path: Path) -> None:
    """The key #66 property: chunk A parked pending (slot released) must not prevent
    chunk B's hub node from running to completion on its own pass."""
    hub = build_hub(tmp_path)
    chunk_a, build_a, _graph_a = _to_poll_merge_node(
        hub, {"source": "default", "ref": "poll-a"}, graph_yaml=ALWAYS_PENDING_GRAPH_YAML
    )
    assert submit_build_pass(hub, chunk_a, build_a, 1).json()["outcome"] == "hub_node_taken"
    assert hub.client.get(f"/api/chunks/{chunk_a}").json()["pending"] is not None  # parked, slot released

    # B's graph, answering `success`, is minted after A's, so a chunk ingested now pins it.
    hub.clock.advance(timedelta(seconds=1))
    chunk_b, build_b, _graph_b = to_merge_node(hub, pointer={"source": "default", "ref": "poll-b"})
    assert submit_build_pass(hub, chunk_b, build_b, 1).json()["outcome"] == "hub_node_taken"

    assert hub.client.get(f"/api/chunks/{chunk_b}").json()["status"] == "done"
    # A is still parked pending, unaffected.
    assert hub.client.get(f"/api/chunks/{chunk_a}").json()["pending"] is not None


def test_poll_timeout_routes_the_failure_edge_via_the_kickback_path(tmp_path: Path) -> None:
    """Pending past ``poll_timeout`` stops polling and kicks back via #64's bounce
    accounting — a bounce fact recorded, routed to the ``failure`` edge's target
    (``build``), below the fleet-wide default ``bounce_cap`` so no escalation yet."""
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, _graph = _to_poll_merge_node(
        hub, {"source": "default", "ref": "poll-timeout"}, graph_yaml=ALWAYS_PENDING_GRAPH_YAML
    )

    apply = submit_build_pass(hub, chunk_id, build_node_id, 1)
    assert apply.json()["outcome"] == "hub_node_taken"  # poll #1 at t0

    hub.clock.advance(timedelta(seconds=31))
    assert hub.turn_outcome(chunk_id) == "pending"  # poll #2 at t0+31
    hub.clock.advance(timedelta(seconds=31))
    assert hub.turn_outcome(chunk_id) == "pending"  # poll #3 at t0+62
    assert _passes(hub, chunk_id) == 3

    # poll_timeout: 90, measured from the FIRST poll (t0) — elapsed is now 62 + 31 = 93 >= 90.
    hub.clock.advance(timedelta(seconds=31))
    assert hub.turn_outcome(chunk_id) == "failure"
    # No 4th pass is taken — the timeout check pre-empts it.
    assert _passes(hub, chunk_id) == 3

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "running"
    assert detail["current_node_name"] == "build"
    assert detail["pending"] is None
    assert len(detail["bounces"]) == 1
    assert detail["bounces"][0]["cause"] == "poll-timeout"
    envelope = json.loads(detail["bounces"][0]["envelope"])
    assert "poll_timeout" in envelope["detail"]
    assert envelope["unlanded"] == []
    assert "step" not in envelope
    assert envelope["action"] is None


@pytest.mark.parametrize("landed", [False, True], ids=["unlanded", "landed"])
def test_a_poll_timeout_envelope_lists_a_seeded_commit_until_its_repo_lands(tmp_path: Path, landed: bool) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, graph = _to_poll_merge_node(
        hub, {"source": "default", "ref": "poll-commit"}, graph_yaml=ALWAYS_PENDING_GRAPH_YAML
    )
    _submit_build_pass_with_commit(hub, chunk_id, build_node_id, 1, repo="acme/widget")
    merge_node = graph.node_by_name("merge")
    assert merge_node is not None
    if landed:
        assert record_hub_asset(
            hub,
            chunk_id,
            node_id=merge_node.node_id,
            node_name="merge",
            epoch=1,
            name="merged/acme/widget",
            content="sha",
        )

    hub.clock.advance(timedelta(seconds=91))
    assert hub.turn_outcome(chunk_id) == "failure"

    (bounce,) = hub.client.get(f"/api/chunks/{chunk_id}").json()["bounces"]
    unlanded = [u["repo"] for u in json.loads(bounce["envelope"])["unlanded"]]
    assert unlanded == ([] if landed else ["acme/widget"])


def test_poll_timeout_escalates_once_the_bounce_cap_is_crossed(tmp_path: Path) -> None:
    """Repeated poll-timeouts are bounces (#64): crossing ``bounce_cap`` escalates
    ``needs_human`` instead of routing back, exactly as a repeated conflict would."""
    graph_yaml = ALWAYS_PENDING_GRAPH_YAML.replace("poll_timeout: 90", "poll_timeout: 30\n    bounce_cap: 1")
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, _graph = _to_poll_merge_node(
        hub, {"source": "default", "ref": "poll-cap"}, graph_yaml=graph_yaml
    )

    # Bounce #1 (poll_timeout: 30, one poll at t0 then straight past the bound).
    submit_build_pass(hub, chunk_id, build_node_id, 1)
    hub.clock.advance(timedelta(seconds=31))
    assert hub.turn_outcome(chunk_id) == "failure"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "running"  # below the cap (1) — routed back, not escalated
    assert len(detail["bounces"]) == 1

    # Bounce #2 escalates; advance the clock for the transition tie-break.
    # Build mints above the hub-owned exit epoch (2).
    hub.clock.advance(timedelta(seconds=1))
    report_lease(hub, chunk_id, epoch=3, seq=2)
    second_build_node = hub.client.get(f"/api/chunks/{chunk_id}").json()["current_node_id"]
    submit_build_pass(hub, chunk_id, second_build_node, 3)
    hub.clock.advance(timedelta(seconds=31))
    assert hub.turn_outcome(chunk_id) == "escalated"
    detail2 = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail2["status"] == "needs_human"
    assert len(detail2["bounces"]) == 2
    # A hub-authored escalation composes no wrapped command — the hub has no runner
    # runtime dir to draw one from (`blizzard-context:/domain/humans/escalation.md` §What each origin carries).
    assert detail2["escalation"]["takeover_command"] == ""
    assert detail2["escalation"]["wrapped_takeover_command"] == ""
    assert detail2["escalation"]["cause"] == "bounce-cap"
    assert detail2["escalation"]["detail"] == "bounce cap (1) crossed after 2 bounces"


def test_the_visit_names_every_work_item_by_its_forge_reference_and_links_the_board(tmp_path: Path) -> None:
    """A chunk mixing a forge-sourced item and a built-in ``hub`` item hands the pass one
    ``{label, reference}`` per item, in ``work_refs`` order, resolved with no forge read — the
    ``hub`` item has no forge reference to give."""
    recorded = _Recorded()
    source = FakeWorkSource(repo="acme/widget")
    hub = _hub_with(tmp_path, recorded, work_sources={"default": source}, public_url="https://blizzard.example.com")
    graph_yaml = _gated("test.recorded")
    assert hub.client.post("/api/graphs", json={"definition_yaml": graph_yaml}).status_code == 201
    minted = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()
    hub_ref = minted["ref"]
    # Minting seats the item on its own chunk; stopping that chunk frees the ref to ride along.
    assert hub.client.post(f"/api/chunks/{minted['chunk_id']}/stop", json={"by": "operator"}).status_code == 202
    chunk_id, build_node_id, _graph = to_merge_node(hub, graph_yaml=graph_yaml, tokens=["default:42", f"hub:{hub_ref}"])
    fetched_before = list(source.fetched)

    assert submit_build_pass(hub, chunk_id, build_node_id, 1).json()["outcome"] == "hub_node_taken"

    (visit,) = recorded.visits
    assert [dict(item) for item in visit.work_items] == [
        {"label": "default#42", "reference": "acme/widget#42"},
        {"label": f"hub:{hub_ref}", "reference": None},
    ]
    assert visit.public_url == "https://blizzard.example.com"
    # Only the feature title's one read of the first item — the references read nothing.
    assert source.fetched == [*fetched_before, "42"]
