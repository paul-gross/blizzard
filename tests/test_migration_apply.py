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


def _migrate(hub, chunk_id: str, node_id: str, *, epoch: int = 1) -> dict:  # type: ignore[no-untyped-def]
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
    assert len(facts.migrations) == 1  # exactly one migration fact, no duplicate
