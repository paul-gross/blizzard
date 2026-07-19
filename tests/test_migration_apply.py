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

from blizzard.hub.config import ROUTE_TOKEN_ENFORCE
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


def _setup(hub, *, target_name: str, mint_target: bool) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    """Mint the source graph (and optionally the target), ingest + promote + claim a
    chunk on the source. Returns (chunk_id, from_node_id)."""
    assert (
        hub.client.post("/api/graphs", json={"definition_yaml": _SRC_YAML.format(target=target_name)}).status_code
        == 201
    )
    if mint_target:
        assert hub.client.post("/api/graphs", json={"definition_yaml": _TARGET_YAML}).status_code == 201
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


def test_a_replayed_migration_completion_is_idempotent_under_route_token_enforce(tmp_path: Path) -> None:
    """Bug #108: ``record_migration`` releases the route as part of landing, so a
    lost-ack replay of an accepted migration presents a token whose route the migration
    itself released. The ``accepted_migration`` natural-key probe must short-circuit to
    the replay response *ahead of* the route-token check, even under
    ``route_token_mode=enforce`` — the same lost-ack replay the warn-mode idempotency
    test above proves, but now with token enforcement in play."""
    hub = build_hub(tmp_path, route_token_mode=ROUTE_TOKEN_ENFORCE)
    chunk_id, node_id, token = _setup_under_enforce(hub, target_name="triage", mint_target=True)

    first = _migrate_with_token(hub, chunk_id, node_id, route_token=token)
    assert first.status_code == 200, first.text
    assert first.json()["outcome"] == "migrated"

    # A re-flushed completion (lost ack) carries the IDENTICAL token — the migration's own
    # completion already released the route, but the replay's natural key matches the
    # accepted migration, so it short-circuits above the token check rather than failing.
    second = _migrate_with_token(hub, chunk_id, node_id, route_token=token)
    assert second.status_code == 200, second.text
    assert second.json()["outcome"] == "migrated"

    facts = hub.services.chunks.load_facts(chunk_id)
    assert facts is not None
    assert len(facts.migrations) == 1  # exactly one migration fact, no duplicate re-pin


def test_a_non_matching_submission_over_a_released_migration_route_is_still_rejected(tmp_path: Path) -> None:
    """Bug #108's carve-out is scoped to the ACCEPTED migration's own natural key only —
    a fresh, non-matching submission (different epoch here) presented with the same
    now-released token is still rejected by the route-token check, exactly as a fresh
    zombie completion would be."""
    hub = build_hub(tmp_path, route_token_mode=ROUTE_TOKEN_ENFORCE)
    chunk_id, node_id, token = _setup_under_enforce(hub, target_name="triage", mint_target=True)

    landed = _migrate_with_token(hub, chunk_id, node_id, route_token=token)
    assert landed.json()["outcome"] == "migrated"

    # Same chunk_id/from_node_id, but a different epoch — no longer matches the accepted
    # migration's (chunk_id, from_node_id, epoch) natural key, so the probe doesn't
    # short-circuit; the released token is rejected by the route-token check first.
    mismatched = _migrate_with_token(hub, chunk_id, node_id, epoch=2, route_token=token)

    assert mismatched.status_code == 200, mismatched.text
    assert mismatched.json()["outcome"] == "failure"
