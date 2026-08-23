"""Cross-graph migration through the apply path + edge caller (issue #90, Phase 4).

A completion whose choice targets another graph records a migration (re-pin + route
release + MIGRATED); a subsequent claim builds the target graph's landing-node envelope;
an unresolvable target escalates to ``needs_human``; and a replay is idempotent.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from blizzard.hub.config import ROUTE_TOKEN_ENFORCE
from tests.support import build_hub, emitted_events, pointer_token, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "9"}

# A source graph whose ``build`` node can migrate to another graph, named
# ``default-delivery`` so ingest pins it; ``pass`` targets ``graph:triage``, ``fail`` retries.
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

# A target graph whose landing node (name-matching the source's `build`) is hub-executed
# (issue #111): `success` routes onward to a runner node, not straight to `done`.
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

# A gate-source graph whose human gate's resolved choice is itself the cross-graph
# migration (issue #90 M1): the resolving migration must close the gate's decision.
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

# Like ``_GATE_SRC_YAML`` but ``approve`` targets ``graph:ghost``, an unminted graph, so
# the migration escalates — the gate's decision must still close (issue #110).
_GATE_SRC_GHOST_YAML = _GATE_SRC_YAML.replace("to: graph:triage", "to: graph:ghost")


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


def _setup_under_enforce(hub, *, target_name: str, mint_target: bool) -> tuple[str, str, str]:  # type: ignore[no-untyped-def]
    """Like ``_setup``, but claims through ``/api/fleet/routes`` and returns the
    plaintext route token too — for driving migration replay under
    ``route_token_mode=enforce`` (issue #108)."""
    assert (
        hub.client.post("/api/graphs", json={"definition_yaml": _SRC_YAML.format(target=target_name)}).status_code
        == 201
    )
    if mint_target:
        assert hub.client.post("/api/graphs", json={"definition_yaml": _TARGET_YAML}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    hub.client.post(f"/api/chunks/{chunk_id}/promote")
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()
    node_id = claim["envelope"]["node"]["node_id"]
    token = str(claim["route_token"])
    report_lease(hub, chunk_id, epoch=1, seq=1, route_token=token)
    return chunk_id, node_id, token


def _migrate_with_token(  # type: ignore[no-untyped-def]
    hub, chunk_id: str, node_id: str, *, epoch: int = 1, route_token: str
) -> httpx.Response:
    """Like ``_migrate``, but carries a ``route_token`` (issue #108) — for driving the
    completion under ``route_token_mode=enforce``."""
    return hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "migrate",
            "epoch": epoch,
            "runner_id": "r1",
            "from_node_id": node_id,
            "route_token": route_token,
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
    # Issue #144 retargeted the re-pin onto `default_model`: the authored choice `model:`
    # is still a single string, landing as the list's one entry.
    assert detail["default_model"] == ["claude-sonnet-5"]  # per-choice model re-pin
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
    hub node's `run:` steps never driven (no holding runner left to poll `hub-advance`)."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _setup(hub, target_name="triage", mint_target=True, target_yaml=_HUB_TARGET_YAML)
    triage_id = next(g["graph_id"] for g in hub.client.get("/api/graphs").json() if g["name"] == "triage")

    resp = _migrate(hub, chunk_id, node_id)

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "hub_node_taken"  # not "migrated" — the runner keeps holding

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == triage_id  # still re-pinned to the target graph
    # The route was RETAINED, not released: `success` routed onward to a non-terminal
    # runner node, so the chunk derives `running`, not `ready`.
    assert detail["status"] == "running"
    assert detail["current_node_name"] == "review"
    # The triage node's reasoning asset still carried across the migration.
    assert any(a["name"] == "triage-notes" for a in detail["artifacts"])
    # The landed hub node's inline run recorded its own run-step log artifact (#65).
    assert any(a["name"].startswith("hub-log.") for a in detail["artifacts"])

    # The observable consequence of a retained route: a fresh claim on this same chunk
    # loses the race — 409, not a hand-out of the landed node.
    conflict = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r2", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert conflict.status_code == 409, conflict.text
    # Nor does the chunk appear as a claimable ready chunk in the queue.
    assert all(e["chunk_id"] != chunk_id for e in hub.client.get("/api/queue").json()["entries"])


def test_an_unresolvable_cross_graph_target_escalates_to_needs_human(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    # The target `ghost` is never minted — the edge caller resolves it to None.
    chunk_id, node_id = _setup(hub, target_name="ghost", mint_target=False)

    resp = _migrate(hub, chunk_id, node_id)

    assert resp.status_code == 200
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    # No migration happened; the chunk derives needs_human (visible on the board), rather
    # than crashing or silently dropping the completion.
    assert detail["status"] == "needs_human"
    # The hub has no runner runtime to compose a wrapped command from, so it stays
    # empty; see `blizzard-context:/domain/humans/escalation.md` §What each origin carries.
    escalation = detail["escalation"]
    assert escalation["wrapped_takeover_command"] == ""
    assert "mint a graph named `ghost`" in escalation["takeover_command"]


def test_a_retired_cross_graph_target_escalates_to_needs_human_exactly_like_an_absent_one(tmp_path: Path) -> None:
    """Retiring `triage`'s only minted version leaves the name with zero non-retired
    candidates, resolving to ``None`` exactly like an unminted name (issue #101)."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _setup(hub, target_name="triage", mint_target=True)
    target_graph_id = hub.client.get("/api/graphs").json()
    triage_id = next(g["graph_id"] for g in target_graph_id if g["name"] == "triage")

    retire = hub.client.post(f"/api/graphs/{triage_id}/retire", json={"by": "operator"})
    assert retire.status_code == 202, retire.text
    assert hub.client.get(f"/api/graphs/{triage_id}").json()["retired"] is True

    resp = _migrate(hub, chunk_id, node_id)

    assert resp.status_code == 200
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
    ``migrated`` (issue #111) — releasing the retained route here would strand the
    landed hub node with nothing to drive it."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _setup(hub, target_name="triage", mint_target=True, target_yaml=_HUB_TARGET_YAML)

    first = _migrate(hub, chunk_id, node_id)
    assert first.json()["outcome"] == "hub_node_taken"
    # A re-flushed completion (lost ack) replays to hub_node_taken, never migrated, and
    # lands no second migration.
    second = _migrate(hub, chunk_id, node_id)
    assert second.json()["outcome"] == "hub_node_taken"

    facts = hub.services.chunks.load_facts(chunk_id)
    assert facts is not None
    assert len(facts.migrations) == 1  # exactly one migration fact, no duplicate


def test_fresh_migration_publishes_queue_changed(tmp_path: Path) -> None:
    """A fresh cross-graph migration re-queues the chunk under the target graph — like
    every other re-admit path, that must publish ``queue-changed`` (issue #107)."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _setup(hub, target_name="triage", mint_target=True)
    since = hub.events.latest_id()

    resp = _migrate(hub, chunk_id, node_id)

    assert resp.json()["outcome"] == "migrated"
    types = [e["event"] for e in emitted_events(hub, since=since)]
    assert "queue-changed" in types


def test_replayed_migration_does_not_publish_queue_changed(tmp_path: Path) -> None:
    """A replayed migration completion (lost ack) is idempotent and re-pins nothing — it
    must not publish a second ``queue-changed`` (issue #107)."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _setup(hub, target_name="triage", mint_target=True)
    first = _migrate(hub, chunk_id, node_id)
    assert first.json()["outcome"] == "migrated"
    since = hub.events.latest_id()

    second = _migrate(hub, chunk_id, node_id)

    assert second.json()["outcome"] == "migrated"
    types = [e["event"] for e in emitted_events(hub, since=since)]
    assert "queue-changed" not in types


def test_a_hub_landing_migration_does_not_publish_queue_changed(tmp_path: Path) -> None:
    """A migration landing on a hub node (issue #111) retains the route and returns
    ``HUB_NODE_TAKEN`` rather than re-queuing — the ``MIGRATED``-keyed guard must not
    fire here (issue #107)."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _setup(hub, target_name="triage", mint_target=True, target_yaml=_HUB_TARGET_YAML)
    since = hub.events.latest_id()

    resp = _migrate(hub, chunk_id, node_id)

    assert resp.json()["outcome"] == "hub_node_taken"
    types = [e["event"] for e in emitted_events(hub, since=since)]
    assert "queue-changed" not in types


def test_a_human_gate_resolved_migration_closes_its_decision(tmp_path: Path) -> None:
    """A human gate whose resolved choice migrates cross-graph must close its decision
    (issue #90 M1) — a migration writes no ``transitions`` row, so without threading the
    ``decision_id`` the resolved decision would stay ``transitioned=False`` forever."""
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
    assert hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "approve"}).status_code == 200
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
    # M1: the gate's decision is closed.
    assert detail["decision"] is None
    assert hub.client.get("/api/decisions").json()["decisions"] == []
    closed = hub.services.chunks.get_decision(decision_id)
    assert closed is not None and closed.transitioned is True


def test_a_human_gate_resolved_migration_to_an_unresolvable_target_closes_its_decision(tmp_path: Path) -> None:
    """A human gate whose resolved choice migrates to an unresolvable target (issue
    #110) must still close its decision — this branch writes neither a transition nor a
    migration fact, so the decision_id must thread onto the escalation instead."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _GATE_SRC_GHOST_YAML}).status_code == 201
    # `graph:ghost` is never minted — the edge caller resolves the target to None.

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

    # A person approves; the holding runner submits the resolving completion. The choice
    # targets graph:ghost, which resolves to None -> the migration ESCALATES.
    assert hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "approve"}).status_code == 200
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
    assert resp.json()["outcome"] == "parked_at_gate", resp.text

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "needs_human"  # escalation recorded, visible on the board
    # #110: the gate's decision is closed atomically with the escalation.
    assert detail["decision"] is None
    assert hub.client.get("/api/decisions").json()["decisions"] == []
    closed = hub.services.chunks.get_decision(decision_id)
    assert closed is not None and closed.transitioned is True


def test_a_replayed_migration_completion_is_idempotent_under_route_token_enforce(tmp_path: Path) -> None:
    """Bug #108: a lost-ack replay of an accepted migration presents a token whose route
    the migration itself already released, so the ``accepted_migration`` natural-key
    probe must short-circuit ahead of the route-token check, even under enforce mode."""
    hub = build_hub(tmp_path, route_token_mode=ROUTE_TOKEN_ENFORCE)
    chunk_id, node_id, token = _setup_under_enforce(hub, target_name="triage", mint_target=True)

    first = _migrate_with_token(hub, chunk_id, node_id, route_token=token)
    assert first.status_code == 200, first.text
    assert first.json()["outcome"] == "migrated"

    # A re-flushed completion carries the IDENTICAL token — the replay's natural key
    # matches the accepted migration, so it short-circuits above the token check.
    second = _migrate_with_token(hub, chunk_id, node_id, route_token=token)
    assert second.status_code == 200, second.text
    assert second.json()["outcome"] == "migrated"

    facts = hub.services.chunks.load_facts(chunk_id)
    assert facts is not None
    assert len(facts.migrations) == 1  # exactly one migration fact, no duplicate re-pin


def test_a_non_matching_submission_over_a_released_migration_route_is_still_rejected(tmp_path: Path) -> None:
    """Bug #108's carve-out is scoped to the accepted migration's own natural key only —
    a non-matching submission over the same released token is still rejected by the
    route-token check, pinned via the ``"live route"`` detail, not just ``"failure"``."""
    hub = build_hub(tmp_path, route_token_mode=ROUTE_TOKEN_ENFORCE)
    chunk_id, node_id, token = _setup_under_enforce(hub, target_name="triage", mint_target=True)

    landed = _migrate_with_token(hub, chunk_id, node_id, route_token=token)
    assert landed.json()["outcome"] == "migrated"

    # A different epoch no longer matches the accepted migration's natural key, so the
    # released token is rejected by the route-token check first, pinned via "live route".
    mismatched = _migrate_with_token(hub, chunk_id, node_id, epoch=2, route_token=token)

    assert mismatched.status_code == 200, mismatched.text
    assert mismatched.json()["outcome"] == "failure"
    assert "live route" in mismatched.json()["detail"]

    # Same-epoch case: only the different from_node_id keeps it off the natural key, so
    # the route-token check still rejects it first, ahead of any graph-node lookup.
    non_matching_node = _migrate_with_token(hub, chunk_id, "nd_does_not_match", epoch=1, route_token=token)

    assert non_matching_node.status_code == 200, non_matching_node.text
    assert non_matching_node.json()["outcome"] == "failure"
    assert "live route" in non_matching_node.json()["detail"]
