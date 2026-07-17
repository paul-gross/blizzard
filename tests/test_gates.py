"""Human gates on the wired hub — component tier (D-045/D-032/D-067).

The gate mechanics against a fully-wired hub over a tmp sqlite store (doubles only at
the forge/PM seams — ``bzh:pluggable-seams``): a graph gate opens a decision on arrival
and rejects a worker transition out of it; a runner-config gate submits a decision in
place of a transition for a worker node; resolution is first-write-wins; the resolving
transition advances the chunk; and ``requeue`` closes an escalation by supersession.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from tests.support import assert_all_timestamps_utc, build_hub, pointer_token, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "7"}

# build (worker) -> approve-gate (human) -> deliver (hub). The gate is the D-032 shape
# from the design sample graph, reduced to the minimum this suite drives.
_GATE_YAML = """
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
          to: approve-gate
        fail:
          description: Incomplete.
          to: build
  approve-gate:
    executor: runner
    judgement:
      by: human
      choices:
        approve:
          description: Ship it — proceed to delivery.
          to: deliver
        reject:
          description: Send it back to build.
          to: build
  deliver:
    executor: hub
    mode: merge-to-main
"""

# A gateless build -> deliver graph, for the runner-config gate (gating a WORKER node).
_PLAIN_YAML = """
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
          to: deliver
        fail:
          description: Incomplete.
          to: build
  deliver:
    executor: hub
    mode: merge-to-main
"""

_BUILD_ARTIFACT = {
    "name": "acme/widget",
    "kind": "git_commit",
    "repo": "acme/widget",
    "branch_name": "b",
    "commit_hash": "c",
}


def _ingest(hub, yaml_body: str) -> tuple[str, dict]:  # type: ignore[no-untyped-def]
    """Mint the graph, ingest a chunk, and return (chunk_id, node-name -> node_id)."""
    graph = hub.client.post("/api/graphs", json={"definition_yaml": yaml_body})
    assert graph.status_code == 201, graph.text
    nodes = {n["name"]: n["node_id"] for n in graph.json()["nodes"]}
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202  # ready to claim (D-103)
    return chunk_id, nodes


def _claim_and_lease(hub, chunk_id: str, *, epoch: int = 1, seq: int = 1) -> None:  # type: ignore[no-untyped-def]
    hub.client.post(
        "/api/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    report_lease(hub, chunk_id, epoch=epoch, seq=seq)


def _completion(node_id: str, *, choice: str, epoch: int = 1, decision_id: str | None = None, artifacts=None) -> dict:
    body: dict = {
        "choice": choice,
        "epoch": epoch,
        "runner_id": "r1",
        "from_node_id": node_id,
        "artifacts": artifacts if artifacts is not None else [],
    }
    if decision_id is not None:
        body["decision_id"] = decision_id
    return body


# --------------------------------------------------------------------------- #
# Graph gate
# --------------------------------------------------------------------------- #


def test_graph_gate_opens_a_decision_and_parks(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, nodes = _ingest(hub, _GATE_YAML)
    _claim_and_lease(hub, chunk_id)

    # build passes -> the transition lands on the human gate: the hub opens a decision.
    resp = hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json=_completion(nodes["build"], choice="pass", artifacts=[_BUILD_ARTIFACT]),
    )
    assert resp.json()["outcome"] == "parked_at_gate", resp.text

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "waiting_on_human"
    decision = detail["decision"]
    assert decision is not None and decision["node_name"] == "approve-gate"
    assert {c["name"] for c in decision["choices"]} == {"approve", "reject"}

    # The open decision is surfaced fleet-wide.
    resp = hub.client.get("/api/decisions")
    open_list = resp.json()["decisions"]
    assert [d["decision_id"] for d in open_list] == [decision["decision_id"]]
    assert_all_timestamps_utc(resp.json())  # bzh:utc-instants — submitted_at


def test_worker_transition_out_of_a_gate_is_rejected(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, nodes = _ingest(hub, _GATE_YAML)
    _claim_and_lease(hub, chunk_id)
    hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json=_completion(nodes["build"], choice="pass", artifacts=[_BUILD_ARTIFACT]),
    )

    # A plain worker transition OUT of the human-judged gate (no decision_id) is rejected.
    resp = hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json=_completion(nodes["approve-gate"], choice="approve"),
    )
    assert resp.json()["outcome"] == "failure"
    assert "human signoff required" in resp.json()["detail"]
    # Still parked — nothing advanced.
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "waiting_on_human"


def test_decide_then_resolving_transition_advances_the_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, nodes = _ingest(hub, _GATE_YAML)
    _claim_and_lease(hub, chunk_id)
    hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json=_completion(nodes["build"], choice="pass", artifacts=[_BUILD_ARTIFACT]),
    )
    decision_id = hub.client.get(f"/api/chunks/{chunk_id}").json()["decision"]["decision_id"]

    # A person decides — first-write-wins.
    resolve = hub.client.post(f"/api/decisions/{decision_id}/resolution", json={"choice": "approve"})
    assert resolve.status_code == 200, resolve.text
    assert_all_timestamps_utc(resolve.json())  # bzh:utc-instants — resolved_at
    # Resolved: no longer waiting_on_human (route still live -> running), decision resolved.
    mid = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert mid["status"] == "running"
    assert mid["decision"]["resolved_choice"] == "approve" and mid["decision"]["transitioned"] is False

    # The holding runner records the resolving transition referencing the decision.
    resolving = hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json=_completion(nodes["approve-gate"], choice="approve", decision_id=decision_id),
    )
    # approve -> deliver (a hub node): the coordinator lands the build artifact.
    assert resolving.json()["outcome"] == "hub_node_taken", resolving.text
    done_resp = hub.client.get(f"/api/chunks/{chunk_id}")
    done = done_resp.json()
    assert done["status"] == "done"
    assert_all_timestamps_utc(done_resp.json())  # bzh:utc-instants — transitions[].recorded_at
    assert hub.forge.landed and hub.forge.landed[0].repo == "acme/widget"
    # The decision is now transitioned; it drops off the chunk's live decision + the open list.
    assert done["decision"] is None
    assert hub.client.get("/api/decisions").json()["decisions"] == []


def test_resolution_is_first_write_wins(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, nodes = _ingest(hub, _GATE_YAML)
    _claim_and_lease(hub, chunk_id)
    hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json=_completion(nodes["build"], choice="pass", artifacts=[_BUILD_ARTIFACT]),
    )
    decision_id = hub.client.get(f"/api/chunks/{chunk_id}").json()["decision"]["decision_id"]

    first = hub.client.post(
        f"/api/decisions/{decision_id}/resolution", json={"choice": "approve", "resolved_by": "ada"}
    )
    assert first.status_code == 200
    second = hub.client.post(
        f"/api/decisions/{decision_id}/resolution", json={"choice": "reject", "resolved_by": "bob"}
    )
    assert second.status_code == 409
    assert second.json()["already_resolved_by"] == "ada"


def test_decide_unknown_choice_is_400(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, nodes = _ingest(hub, _GATE_YAML)
    _claim_and_lease(hub, chunk_id)
    hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json=_completion(nodes["build"], choice="pass", artifacts=[_BUILD_ARTIFACT]),
    )
    decision_id = hub.client.get(f"/api/chunks/{chunk_id}").json()["decision"]["decision_id"]
    resp = hub.client.post(f"/api/decisions/{decision_id}/resolution", json={"choice": "maybe"})
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Runner-config gate (a worker node, gated by the runner)
# --------------------------------------------------------------------------- #


def test_runner_config_gate_submits_a_decision_for_a_worker_node(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, nodes = _ingest(hub, _PLAIN_YAML)
    _claim_and_lease(hub, chunk_id)

    # The runner gates `build` by config: instead of a transition it POSTs a decision,
    # carrying the step's artifacts; the choice set is the node's own (pass/fail).
    resp = hub.client.post(
        f"/api/chunks/{chunk_id}/decisions",
        json={"from_node_id": nodes["build"], "epoch": 1, "runner_id": "r1", "artifacts": [_BUILD_ARTIFACT]},
    )
    assert resp.json()["outcome"] == "parked_at_gate", resp.text

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "waiting_on_human"
    decision = detail["decision"]
    assert decision["node_name"] == "build"
    assert {c["name"] for c in decision["choices"]} == {"pass", "fail"}
    # The gated step's artifact committed atomically with the decision.
    assert any(a["name"] == "acme/widget" for a in detail["artifacts"])

    # Resolve pass, then the resolving transition (from the worker node, decision_id set)
    # advances build -> deliver.
    hub.client.post(f"/api/decisions/{decision['decision_id']}/resolution", json={"choice": "pass"})
    resolving = hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json=_completion(nodes["build"], choice="pass", decision_id=decision["decision_id"]),
    )
    assert resolving.json()["outcome"] == "hub_node_taken", resolving.text
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"


def test_runner_config_gate_submission_is_idempotent(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, nodes = _ingest(hub, _PLAIN_YAML)
    _claim_and_lease(hub, chunk_id)
    body = {"from_node_id": nodes["build"], "epoch": 1, "runner_id": "r1", "artifacts": [_BUILD_ARTIFACT]}
    hub.client.post(f"/api/chunks/{chunk_id}/decisions", json=body)
    hub.client.post(f"/api/chunks/{chunk_id}/decisions", json=body)  # replay (lost ack)
    # Exactly one decision open — the natural-key probe deduped the replay.
    assert len(hub.client.get("/api/decisions").json()["decisions"]) == 1


# --------------------------------------------------------------------------- #
# Requeue supersession
# --------------------------------------------------------------------------- #


def test_requeue_closes_an_escalation_by_supersession(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _ = _ingest(hub, _PLAIN_YAML)
    _claim_and_lease(hub, chunk_id)
    # Report an escalation (retries exhausted) — the chunk derives needs_human.
    esc = hub.client.post(
        f"/api/chunks/{chunk_id}/escalations",
        json={"epoch": 1, "runner_id": "r1", "takeover_command": "cd env && claude --resume s"},
    )
    assert esc.status_code == 202
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "needs_human"

    # Requeue supersedes the escalation and releases the route -> the chunk is ready again.
    # (The requeue fact must post-date the escalation; advance the test clock past the tie.)
    hub.clock.advance(timedelta(seconds=1))
    rq = hub.client.post(f"/api/chunks/{chunk_id}/requeues")
    assert rq.status_code == 202, rq.text
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    # It re-enters the ready queue for a fresh claim at its current node.
    peek = hub.client.get("/api/queue/peek").json()
    assert any(e["chunk_id"] == chunk_id for e in peek["entries"])


def test_requeue_on_a_non_escalated_chunk_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _ = _ingest(hub, _PLAIN_YAML)
    _claim_and_lease(hub, chunk_id)  # running, not escalated
    resp = hub.client.post(f"/api/chunks/{chunk_id}/requeues")
    assert resp.status_code == 409


# --------------------------------------------------------------------------- #
# Detach (D-088)
# --------------------------------------------------------------------------- #


def test_detach_a_claimed_chunk_re_derives_ready_and_reenters_the_queue(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _ = _ingest(hub, _PLAIN_YAML)
    _claim_and_lease(hub, chunk_id)
    # No clock.advance: the route creation and the release land at the same fixed
    # instant. The route-event seq tiebreak (issue #41), not the timestamp, is what
    # decides the tie — see test_detach_at_a_same_instant_tie_still_takes_effect.
    resp = hub.client.post(f"/api/chunks/{chunk_id}/detach")
    assert resp.status_code == 202, resp.text
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    # The detached chunk re-enters the ready queue, claimable at its current node.
    peek = hub.client.get("/api/queue/peek").json()
    assert any(e["chunk_id"] == chunk_id for e in peek["entries"])


def test_detach_an_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/chunks/does-not-exist/detach")
    assert resp.status_code == 404


def test_detach_a_ready_unclaimed_chunk_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _ = _ingest(hub, _PLAIN_YAML)  # promoted (ready), never claimed -> no live route
    resp = hub.client.post(f"/api/chunks/{chunk_id}/detach")
    assert resp.status_code == 409


def test_detach_twice_is_409_the_second_time(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _ = _ingest(hub, _PLAIN_YAML)
    _claim_and_lease(hub, chunk_id)
    first = hub.client.post(f"/api/chunks/{chunk_id}/detach")
    assert first.status_code == 202, first.text
    # The route is already released; detach is deliberately not silently idempotent.
    second = hub.client.post(f"/api/chunks/{chunk_id}/detach")
    assert second.status_code == 409


def test_detach_an_escalated_chunk_succeeds_and_the_escalation_survives(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _ = _ingest(hub, _PLAIN_YAML)
    _claim_and_lease(hub, chunk_id)
    esc = hub.client.post(
        f"/api/chunks/{chunk_id}/escalations",
        json={"epoch": 1, "runner_id": "r1", "takeover_command": "cd env && claude --resume s"},
    )
    assert esc.status_code == 202
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "needs_human"

    resp = hub.client.post(f"/api/chunks/{chunk_id}/detach")
    assert resp.status_code == 202, resp.text
    # The runner is released, but detach is not requeue: the escalation stays open.
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "needs_human"


def test_detach_publishes_chunk_changed_and_queue_changed(tmp_path: Path) -> None:
    """The board learns of a detach live, as it does of a requeue (D-088, D-067).

    Both events matter and for different reasons: ``chunk-changed`` carries the chunk's
    re-derived status to any open detail view, and ``queue-changed`` tells the queue view a
    new entry is claimable — a detached chunk re-enters the ready queue."""
    hub = build_hub(tmp_path)
    chunk_id, _ = _ingest(hub, _PLAIN_YAML)
    _claim_and_lease(hub, chunk_id)
    # No clock.advance: the route creation and the release tie on timestamp (issue #41).
    before = len(hub.events.snapshot())

    assert hub.client.post(f"/api/chunks/{chunk_id}/detach").status_code == 202

    published = hub.events.snapshot()[before:]
    assert [e.type for e in published] == ["chunk-changed", "queue-changed"]
    # chunk-changed carries the *re-derived* status, not the pre-detach one.
    assert f'"chunk_id": "{chunk_id}", "status": "ready"' in published[0].framed()


def test_reclaim_at_a_same_instant_tie_still_derives_running(tmp_path: Path) -> None:
    """The other half of issue #41's tie: a fresh claim landing at the exact instant of
    a prior release must not lose the live route. Detach releases the route, and —
    with no ``clock.advance`` — the re-claim lands at the identical fixed instant; the
    route-event seq tiebreak, not the timestamp, is what keeps this a live route
    (generalizes ``test_reclaimed_after_release_is_running_again``, which pins the same
    guarantee at the domain-unit tier)."""
    hub = build_hub(tmp_path)
    chunk_id, _ = _ingest(hub, _PLAIN_YAML)
    _claim_and_lease(hub, chunk_id)
    assert hub.client.post(f"/api/chunks/{chunk_id}/detach").status_code == 202
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"

    # No clock.advance: this claim's route.created ties the just-written route.released.
    reclaim = hub.client.post(
        "/api/routes",
        json={"chunk_id": chunk_id, "runner_id": "r2", "workspace_id": "w2", "environment_ids": ["e"]},
    )
    assert reclaim.status_code == 201, reclaim.text
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "running"
