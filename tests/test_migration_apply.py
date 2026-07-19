"""Cross-graph migration through the apply path + edge caller (issue #90, Phase 4).

Component tier over the real HTTP surface: a completion whose choice targets another
graph records a migration (re-pin + route release + MIGRATED), a subsequent claim builds
the **target** graph's landing-node envelope, an unresolvable target escalates to
``needs_human`` (never a crash), and a replay is idempotent. The edge caller
(``api/fleet.py``) resolves the ``graph:<name>`` target and passes it into a
repo-free ``ApplyService`` (MUST-FIX 2).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from tests.support import build_hub, pointer_token, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "9"}

# A source graph whose ``build`` node can migrate to another graph. Named
# ``default-delivery`` so ingest pins it. ``pass`` targets ``graph:triage`` (a name-match
# landing on triage's own ``build`` node); ``fail`` retries.
_SRC_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: Build.
    judgement:
      prompt: Assess.
      choices:
        migrate:
          description: Hand off to triage.
          to: graph:{target}
          model: claude-sonnet-5
        fail:
          description: Retry.
          to: build
"""

_TARGET_YAML = """
name: triage
entry: build
nodes:
  build:
    executor: runner
    prompt: Triage.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Done.
          to: done
        fail:
          description: Retry.
          to: build
"""

# A target graph whose landing node (name-matching the source's `build`) is
# hub-executed (issue #111) — mirrors `_BUILD_DELIVER_YAML`'s `deliver` node shape
# (a `run:` list + a judgement with `success`/`failure` choices), but under the
# name the source's migrating node lands on by name-match. `success` routes onward
# to a runner node (never straight to `done`) so the inline run's own route-retention
# (a non-terminal hub-step transition releases nothing, `_route`'s
# `release_route=to_node_id == RESERVED_TERMINAL`) is what the test observes, not the
# terminal chunk's own route release.
_HUB_TARGET_YAML = """
name: triage
entry: build
nodes:
  build:
    executor: hub
    run:
      - command: "true"
    judgement:
      choices:
        success:
          description: Delivered.
          to: review
        failure:
          description: Failed to deliver.
          to: build
  review:
    executor: runner
    prompt: Review the delivery.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Done.
          to: done
        fail:
          description: Retry.
          to: build
"""

# A gate-source graph whose **human gate's** resolved choice is itself the cross-graph
# migration (issue #90 M1). build (worker, pass) -> approve-gate (human, approve migrates
# to graph:triage). The resolving migration must close the gate's decision — a migration
# writes no transitions row, so an un-threaded decision would stay live forever.
_GATE_SRC_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: Build.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Ready for signoff.
          to: approve-gate
        fail:
          description: Retry.
          to: build
  approve-gate:
    executor: runner
    judgement:
      by: human
      choices:
        approve:
          description: Hand off to triage.
          to: graph:triage
        reject:
          description: Send back.
          to: build
"""


def _setup(hub, *, target_name: str, mint_target: bool, target_yaml: str = _TARGET_YAML) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    """Mint the source graph (and optionally the target), ingest + promote + claim a
    chunk on the source. Returns (chunk_id, from_node_id)."""
    assert (
        hub.client.post("/api/graphs", json={"definition_yaml": _SRC_YAML.format(target=target_name)}).status_code
        == 201
    )
    if mint_target:
        assert hub.client.post("/api/graphs", json={"definition_yaml": target_yaml}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    hub.client.post(f"/api/chunks/{chunk_id}/promote")
    node_id = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)
    return chunk_id, node_id


def _migrate(hub, chunk_id: str, node_id: str, *, epoch: int = 1) -> httpx.Response:  # type: ignore[no-untyped-def]
    return hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "migrate",
            "epoch": epoch,
            "runner_id": "r1",
            "from_node_id": node_id,
            "artifacts": [{"name": "triage-notes", "kind": "asset", "content": "hand off"}],
        },
    )


def test_a_cross_graph_choice_migrates_repins_and_re_queues_at_the_landing_node(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _setup(hub, target_name="triage", mint_target=True)
    target_graph_id = hub.client.get("/api/graphs").json()  # list; find triage
    triage_id = next(g["graph_id"] for g in target_graph_id if g["name"] == "triage")

    resp = _migrate(hub, chunk_id, node_id)

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "migrated"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == triage_id  # re-pinned to the target graph
    assert detail["status"] == "ready"  # re-queued, claimable — not done/delivering
    assert detail["current_node_name"] == "build"  # name-match landing on triage's build
    assert detail["model"] == "claude-sonnet-5"  # per-choice model re-pin
    # The triage node's reasoning asset carried across (MUST-FIX 1).
    assert any(a["name"] == "triage-notes" for a in detail["artifacts"])

    # A subsequent claim builds the TARGET graph's landing-node envelope — claimable
    # under the new graph.
    envelope = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]
    assert envelope["node"]["node_id"] == detail["current_node_id"]


def test_a_cross_graph_choice_migrating_onto_a_hub_node_runs_it_inline_and_retains_the_route(
    tmp_path: Path,
) -> None:
    """A migration whose landing node is hub-executed (issue #111) must not release the
    route the way a runner-landing migration does — releasing it would leave the landed
    hub node's `run:` steps never driven (no holding runner left to poll `hub-advance`).
    Mirrors `_respond`'s transition-into-a-hub-node branch: run the hub node inline,
    retain the route, and return `HUB_NODE_TAKEN`."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _setup(hub, target_name="triage", mint_target=True, target_yaml=_HUB_TARGET_YAML)
    triage_id = next(g["graph_id"] for g in hub.client.get("/api/graphs").json() if g["name"] == "triage")

    resp = _migrate(hub, chunk_id, node_id)

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "hub_node_taken"  # not "migrated" — the runner keeps holding

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == triage_id  # still re-pinned to the target graph
    # The route was RETAINED, not released: the inline hub run's `success` choice routed
    # onward to a non-terminal runner node (`review`), so the chunk derives `running` (a
    # live route) rather than `ready` (re-queued, claimable) the way a runner-landing
    # migration's target does.
    assert detail["status"] == "running"
    assert detail["current_node_name"] == "review"
    # The triage node's reasoning asset still carried across the migration.
    assert any(a["name"] == "triage-notes" for a in detail["artifacts"])
    # The landed hub node's inline run recorded its own run-step log artifact (#65).
    assert any(a["name"].startswith("hub-log.") for a in detail["artifacts"])

    # The observable consequence of a retained route: a fresh claim on this same chunk
    # loses the race — 409, not a hand-out of the landed node the way the runner-landing
    # test's re-claim succeeds.
    conflict = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r2", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert conflict.status_code == 409, conflict.text
    # Nor does the chunk appear as a claimable ready chunk in the queue.
    assert all(e["chunk_id"] != chunk_id for e in hub.client.get("/api/queue/peek").json()["entries"])


def test_an_unresolvable_cross_graph_target_escalates_to_needs_human(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    # The target `ghost` is never minted — the edge caller resolves it to None.
    chunk_id, node_id = _setup(hub, target_name="ghost", mint_target=False)

    resp = _migrate(hub, chunk_id, node_id)

    assert resp.status_code == 200
    # No migration happened; the chunk derives needs_human (visible on the board), rather
    # than crashing or silently dropping the completion.
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "needs_human"


def test_a_replayed_migration_completion_is_idempotent(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _setup(hub, target_name="triage", mint_target=True)

    first = _migrate(hub, chunk_id, node_id)
    assert first.json()["outcome"] == "migrated"
    # A re-flushed completion (lost ack) replays to MIGRATED without a second re-pin.
    second = _migrate(hub, chunk_id, node_id)
    assert second.json()["outcome"] == "migrated"

    facts = hub.services.chunks.load_facts(chunk_id)
    assert facts is not None
    assert len(facts.migrations) == 1  # exactly one migration fact, no duplicate


def test_a_replayed_hub_landing_migration_completion_returns_hub_node_taken(tmp_path: Path) -> None:
    """A hub-landing migration's lost-ack replay must return ``hub_node_taken``, not
    ``migrated`` (issue #111). The runner reacts to ``migrated`` by RELEASING its route —
    correct for a runner landing (the chunk re-queues ``ready``, claimable) but fatal for a
    hub landing: the route was retained and the chunk derives ``delivering`` (never
    ``ready``), so a released route strands it with nothing to drive the landed hub node.
    The replay therefore returns ``hub_node_taken`` so the holding runner keeps its
    environments and its ADVANCE poll carries the node to its outcome. (This is the fence on
    the crash-sweep wedge ``test_kill9_at_migrate_crash_point_landing_on_a_hub_node`` proves
    end to end: the crash at ``migrate.after-record.before-response`` loses the response, and
    only this replay outcome keeps the retained-route chunk alive on recovery.)"""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _setup(hub, target_name="triage", mint_target=True, target_yaml=_HUB_TARGET_YAML)

    first = _migrate(hub, chunk_id, node_id)
    assert first.json()["outcome"] == "hub_node_taken"
    # A re-flushed completion (lost ack) replays to hub_node_taken — never migrated, which
    # would make the runner release the retained route — and lands no second migration.
    second = _migrate(hub, chunk_id, node_id)
    assert second.json()["outcome"] == "hub_node_taken"

    facts = hub.services.chunks.load_facts(chunk_id)
    assert facts is not None
    assert len(facts.migrations) == 1  # exactly one migration fact, no duplicate


def test_a_human_gate_resolved_migration_closes_its_decision(tmp_path: Path) -> None:
    """A human gate whose resolved choice migrates cross-graph must close its decision
    (issue #90 M1). A migration writes no ``transitions`` row, so without threading the
    ``decision_id`` the resolved decision stays ``transitioned=False`` forever — a phantom
    live decision that mis-renders the board and, worse, wedges REAP recovery (the runner
    skips any chunk whose ``decision`` is non-None as owned by ADVANCE)."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _GATE_SRC_YAML}).status_code == 201
    assert hub.client.post("/api/graphs", json={"definition_yaml": _TARGET_YAML}).status_code == 201
    triage_id = next(g["graph_id"] for g in hub.client.get("/api/graphs").json() if g["name"] == "triage")

    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    hub.client.post(f"/api/chunks/{chunk_id}/promote")
    build_node = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)

    # build passes -> lands on the human gate; a decision opens.
    hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": build_node, "artifacts": []},
    )
    parked = hub.client.get(f"/api/chunks/{chunk_id}").json()
    decision_id = parked["decision"]["decision_id"]
    gate_node = parked["current_node_id"]

    # A person approves; the holding runner submits the resolving completion, which — the
    # choice targeting graph:triage — MIGRATES rather than transitions.
    assert hub.client.post(f"/api/decisions/{decision_id}/resolution", json={"choice": "approve"}).status_code == 200
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "approve",
            "epoch": 1,
            "runner_id": "r1",
            "from_node_id": gate_node,
            "decision_id": decision_id,
            "artifacts": [],
        },
    )
    assert resp.json()["outcome"] == "migrated", resp.text

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == triage_id  # re-pinned to the target graph
    assert detail["status"] == "ready"  # re-queued under triage, claimable
    assert detail["current_node_name"] == "build"  # approve-gate has no match -> triage's entry
    # M1: the gate's decision is closed — nothing left to mis-render or wedge REAP.
    assert detail["decision"] is None
    assert hub.client.get("/api/decisions").json()["decisions"] == []
    closed = hub.services.chunks.get_decision(decision_id)
    assert closed is not None and closed.transitioned is True
