"""``chunk-changed`` frame enrichment across the emit sites (component tier, issue #212).

Every mutating chunk route now publishes through the shared
:mod:`blizzard.hub.api.chunk_events` helper — one representative site per family named
in the plan's Phase 2 acceptance criteria, asserted on the JSON payload the broker
actually recorded (``bzh:facts-not-status`` — the frame is a derivation, so these drive
the real routes rather than calling the broker directly).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blizzard.hub.events.broker import CHUNK_CHANGED
from tests.support import build_hub, emitted_events, pointer_token, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "212"}

_BUILD_DELIVER_YAML = """
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
    run:
      - command: "true"
    judgement:
      choices:
        success:
          description: Delivered.
          to: done
        failure:
          description: Failed to deliver.
          to: build
"""


def _chunk_changed_frames(hub, *, since: int = 0) -> list[dict]:  # type: ignore[no-untyped-def]
    return [json.loads(e["data"]) for e in emitted_events(hub, since=since) if e["event"] == CHUNK_CHANGED]


def _latest_event_id(hub) -> int:  # type: ignore[no-untyped-def]
    events = emitted_events(hub)
    return int(events[-1]["id"]) if events else 0


def _claimed(hub, *, graph_yaml: str = _BUILD_DELIVER_YAML) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    """Mint the graph, claim a route, and report the runner-minted lease (epoch 1)."""
    assert hub.client.post("/api/graphs", json={"definition_yaml": graph_yaml}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    node_id = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)
    return chunk_id, node_id


def test_ingest_frame_carries_graph_id_and_omits_runner_id(tmp_path: Path) -> None:
    """A fresh, never-claimed chunk's frame carries ``graph_id`` (issue #212 AC 4) and
    omits ``runner_id`` entirely — not a ``null`` (AC 5)."""
    hub = build_hub(tmp_path)
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    frames = _chunk_changed_frames(hub)
    assert len(frames) == 1
    frame = frames[0]
    assert frame["chunk_id"] == chunk_id
    assert frame["status"] == "not_ready"
    assert frame["cause"] == "minted"
    assert "runner_id" not in frame
    assert "prev_status" not in frame
    assert "prev_node" not in frame
    assert "graph_id" in frame


def test_claim_carries_cause_claimed_and_runner_id(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _BUILD_DELIVER_YAML}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    before = _latest_event_id(hub)
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    frames = _chunk_changed_frames(hub, since=before)
    assert len(frames) == 1
    frame = frames[0]
    assert frame["cause"] == "claimed"
    assert frame["runner_id"] == "r1"
    assert frame["status"] == "running"


def test_node_completion_carries_prev_node_node_and_status_change(tmp_path: Path) -> None:
    """``_BUILD_DELIVER_YAML``'s ``deliver`` hub node runs synchronously on landing
    (``run: [true]``, its own judgement auto-picks ``success``), so one ``pass``
    completion at ``build`` drives the chunk all the way to the terminal ``done`` in the
    same request — the newest recorded transition is ``deliver -> done``, so ``prev_node``
    is ``deliver`` and ``node`` is omitted (``done`` is the reserved terminal, not a real
    node — the same convention the chunk-detail's own ``current_node_name`` follows)."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claimed(hub)
    before = _latest_event_id(hub)
    completion = {
        "choice": "pass",
        "epoch": 1,
        "runner_id": "r1",
        "from_node_id": node_id,
        "artifacts": [
            {"name": "w", "kind": "git_commit", "repo": "acme/widget", "branch_name": "b", "commit_hash": "c"}
        ],
    }
    resp = hub.client.post(f"/api/fleet/chunks/{chunk_id}/completions", json=completion)
    assert resp.status_code == 200, resp.text
    frames = _chunk_changed_frames(hub, since=before)
    assert frames, "the completion should publish a chunk-changed frame"
    frame = frames[-1]
    assert frame["cause"] == "node-completed"
    assert frame["prev_node"] == "deliver"
    assert "node" not in frame
    assert frame["status"] == "done"
    assert frame["prev_status"] != frame["status"]
    # The terminal transition releases the route (`bzh:facts-not-status`'s
    # `holds_claim`), so `route_of` answers None and the frame omits `runner_id` too —
    # AC 5's "no placeholder junk" holds even on the frame that ends the chunk's life.
    assert "runner_id" not in frame


def test_stop_carries_cause_stopped(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _node_id = _claimed(hub)
    before = _latest_event_id(hub)
    resp = hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "alice"})
    assert resp.status_code == 202, resp.text
    frames = _chunk_changed_frames(hub, since=before)
    assert frames[-1]["cause"] == "stopped"
    assert frames[-1]["status"] == "stopped"


def test_escalation_carries_cause_escalated(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _node_id = _claimed(hub)
    before = _latest_event_id(hub)
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/escalations",
        json={"runner_id": "r1", "epoch": 1, "takeover_command": "cd wd && claude --resume"},
    )
    assert resp.status_code == 202, resp.text
    frames = _chunk_changed_frames(hub, since=before)
    assert frames[-1]["cause"] == "escalated"
    assert frames[-1]["status"] == "needs_human"


def test_group_carries_cause_grouped(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    survivor_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "212a"})]}
    ).json()["chunk_id"]
    merged_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "212b"})]}
    ).json()["chunk_id"]
    before = _latest_event_id(hub)
    resp = hub.client.post(f"/api/chunks/{survivor_id}/group", json={"merge_chunk_ids": [merged_id]})
    assert resp.status_code == 200, resp.text
    frames = _chunk_changed_frames(hub, since=before)
    assert frames[-1]["cause"] == "grouped"
    assert frames[-1]["chunk_id"] == survivor_id


def test_requeue_carries_cause_requeued(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _node_id = _claimed(hub)
    esc = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/escalations",
        json={"runner_id": "r1", "epoch": 1, "takeover_command": "cd wd && claude --resume"},
    )
    assert esc.status_code == 202, esc.text
    before = _latest_event_id(hub)
    resp = hub.client.post(f"/api/chunks/{chunk_id}/requeues")
    assert resp.status_code == 202, resp.text
    frames = _chunk_changed_frames(hub, since=before)
    assert frames[-1]["cause"] == "requeued"


def test_lease_minted_via_events_batch_carries_cause_claimed(tmp_path: Path) -> None:
    """``ingest_runner_facts``' per-fact cause map (issue #212, Phase 2) — a batched
    ``lease.minted`` maps to ``claimed``, distinct from the route-claim's own site."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _BUILD_DELIVER_YAML}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    assert (
        hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
        ).status_code
        == 201
    )
    before = _latest_event_id(hub)
    resp = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [{"seq": 1, "kind": "lease.minted", "payload": {"chunk_id": chunk_id, "epoch": 1}}],
        },
    )
    assert resp.status_code == 200, resp.text
    frames = _chunk_changed_frames(hub, since=before)
    assert frames[-1]["cause"] == "claimed"
