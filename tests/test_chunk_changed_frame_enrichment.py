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

from blizzard.hub.events.broker import CHUNK_CHANGED, EVENT_LOGGED, QUEUE_CHANGED, RUNNER_CHANGED
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


def _frames_of(hub, event_type: str, *, since: int = 0) -> list[dict]:  # type: ignore[no-untyped-def]
    return [json.loads(e["data"]) for e in emitted_events(hub, since=since) if e["event"] == event_type]


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
    # `key` (issue #213) names the mint fact itself — the chunk's own natural key,
    # matching `ActivityRow`'s `chunks:{chunk_id}` format exactly.
    assert frame["key"] == f"chunks:{chunk_id}"


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
    # `key` (issue #213) names the freshly-created route — a table-qualified
    # `route_created:<route_id>` natural key, matching `ActivityRow`'s format exactly.
    assert frame["key"].startswith("route_created:")


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
    # `key` (issue #213) names the freshly-recorded transition (`deliver -> done`).
    assert frame["key"].startswith("transitions:")


def test_stop_carries_cause_stopped(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _node_id = _claimed(hub)
    before = _latest_event_id(hub)
    resp = hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "alice"})
    assert resp.status_code == 202, resp.text
    frames = _chunk_changed_frames(hub, since=before)
    assert frames[-1]["cause"] == "stopped"
    assert frames[-1]["status"] == "stopped"
    # `key` (issue #213) names the freshly-written `chunk_stopped` fact.
    assert frames[-1]["key"].startswith("chunk_stopped:")


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
    # `key` (issue #213) names the freshly-written `escalations` row.
    assert frames[-1]["key"].startswith("escalations:")


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
    # `key` (issue #213) names the freshly-written `chunk_grouped` row.
    assert frames[-1]["key"].startswith("chunk_grouped:")


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
    # `key` (issue #213) names the freshly-written `requeues` row.
    assert frames[-1]["key"].startswith("requeues:")


def test_lease_minted_via_events_batch_carries_cause_claimed(tmp_path: Path) -> None:
    """``ingest_runner_facts``' per-fact cause map (issue #212, Phase 2) — a batched
    ``lease.minted`` maps to ``claimed``, distinct from the route-claim's own site."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _BUILD_DELIVER_YAML}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    claim_since = _latest_event_id(hub)
    assert (
        hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
        ).status_code
        == 201
    )
    claim_key = _chunk_changed_frames(hub, since=claim_since)[-1]["key"]
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
    # `key` (issue #213) names the SAME live route both sites' frames describe — the
    # route-claim's own direct publish and this later lease-mint ingest publish must
    # carry an identical key so Phase 4's frontend dedup recognizes them as one fact.
    assert frames[-1]["key"] == claim_key


def test_pause_and_resume_carry_chunk_pause_facts_key(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _node_id = _claimed(hub)
    before = _latest_event_id(hub)
    pause = hub.client.post(f"/api/chunks/{chunk_id}/pause", json={"by": "alice"})
    assert pause.status_code == 202, pause.text
    resume = hub.client.post(f"/api/chunks/{chunk_id}/resume", json={"by": "alice"})
    assert resume.status_code == 202, resume.text
    frames = _chunk_changed_frames(hub, since=before)
    paused_frame = next(f for f in frames if f["cause"] == "paused")
    resumed_frame = next(f for f in frames if f["cause"] == "resumed")
    # `key` (issue #213) names each fresh `chunk_pause_facts` row — a distinct one per
    # append, even though both are the same "newest-fact-wins" table.
    assert paused_frame["key"].startswith("chunk_pause_facts:")
    assert resumed_frame["key"].startswith("chunk_pause_facts:")
    assert paused_frame["key"] != resumed_frame["key"]


def test_promote_carries_chunk_promoted_key_and_omits_it_on_idempotent_replay(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _BUILD_DELIVER_YAML}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    before = _latest_event_id(hub)
    first = hub.client.post(f"/api/chunks/{chunk_id}/promote")
    assert first.status_code == 202, first.text
    frames = _chunk_changed_frames(hub, since=before)
    assert frames[-1]["cause"] == "promoted"
    # `key` (issue #213) names the freshly-written `chunk_promoted` row.
    assert frames[-1]["key"].startswith("chunk_promoted:")

    # A double-promote is a harmless no-op (already-ready) — the frame still fires
    # (existing behavior, unchanged by this phase), but nothing fresh was recorded, so
    # `key` is genuinely absent rather than pointing at the earlier row.
    before2 = _latest_event_id(hub)
    second = hub.client.post(f"/api/chunks/{chunk_id}/promote")
    assert second.status_code == 202, second.text
    replay_frame = _chunk_changed_frames(hub, since=before2)[-1]
    assert replay_frame["cause"] == "promoted"
    assert "key" not in replay_frame


def test_detach_carries_route_released_key(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _node_id = _claimed(hub)
    before = _latest_event_id(hub)
    resp = hub.client.post(f"/api/chunks/{chunk_id}/detach")
    assert resp.status_code == 202, resp.text
    frames = _chunk_changed_frames(hub, since=before)
    assert frames[-1]["cause"] == "detached"
    # `key` (issue #213) names the freshly-written `route_released` row.
    assert frames[-1]["key"].startswith("route_released:")


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


def test_decision_opened_and_resolved_carry_their_keys(tmp_path: Path) -> None:
    """A graph gate opens ``decision-opened`` on arrival, and its resolution fires both
    ``decision-resolved`` and a ``decision-resolved``-caused ``chunk-changed`` — every one
    of the three named events carries the same decision's key (issue #213)."""
    hub = build_hub(tmp_path)
    gate_yaml = """
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
          description: Ship it.
          to: approve-gate
  approve-gate:
    executor: runner
    judgement:
      by: human
      choices:
        approve:
          description: Proceed.
          to: done
"""
    chunk_id, build_node_id = _claimed(hub, graph_yaml=gate_yaml)
    before = _latest_event_id(hub)
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": build_node_id, "artifacts": []},
    )
    assert resp.status_code == 200, resp.text
    decision_id = hub.client.get(f"/api/chunks/{chunk_id}").json()["decision"]["decision_id"]
    opened = _frames_of(hub, "decision-opened", since=before)
    assert opened[-1]["key"] == f"decisions:{decision_id}"

    before2 = _latest_event_id(hub)
    resolve = hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "approve"})
    assert resolve.status_code == 200, resolve.text
    resolved = _frames_of(hub, "decision-resolved", since=before2)
    assert resolved[-1]["key"] == f"decision_resolutions:{decision_id}"
    changed = _chunk_changed_frames(hub, since=before2)
    assert changed[-1]["cause"] == "decision-resolved"
    assert changed[-1]["key"] == f"decision_resolutions:{decision_id}"


def test_decision_submitted_carries_decisions_key(tmp_path: Path) -> None:
    """A runner-config gate's ``POST .../decisions`` — the ``decision-submitted`` cause."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claimed(hub, graph_yaml=_PLAIN_YAML)
    before = _latest_event_id(hub)
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/decisions",
        json={"from_node_id": node_id, "epoch": 1, "runner_id": "r1", "artifacts": []},
    )
    assert resp.status_code == 200, resp.text
    decision_id = hub.client.get(f"/api/chunks/{chunk_id}").json()["decision"]["decision_id"]
    frames = _chunk_changed_frames(hub, since=before)
    assert frames[-1]["cause"] == "decision-submitted"
    assert frames[-1]["key"] == f"decisions:{decision_id}"


def test_question_asked_and_answered_carry_their_keys(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claimed(hub)
    before = _latest_event_id(hub)
    asked = hub.client.post(
        "/api/questions",
        json={
            "question_id": "qn_213",
            "chunk_id": chunk_id,
            "node_id": node_id,
            "session_id": "sess-1",
            "runner_id": "r1",
            "epoch": 1,
            "question": "Which API?",
            "options": ["rest", "graphql"],
            "asked_at": "2026-07-13T00:00:00+00:00",
        },
    )
    assert asked.status_code == 201, asked.text
    asked_events = _frames_of(hub, "question-asked", since=before)
    assert asked_events[-1]["key"] == "questions:qn_213"
    asked_changed = _chunk_changed_frames(hub, since=before)
    assert asked_changed[-1]["cause"] == "question-asked"
    assert asked_changed[-1]["key"] == "questions:qn_213"

    before2 = _latest_event_id(hub)
    answered = hub.client.post("/api/questions/qn_213/answers", json={"answer": "rest", "answered_by": "alice"})
    assert answered.status_code == 201, answered.text
    answered_events = _frames_of(hub, "question-answered", since=before2)
    assert answered_events[-1]["key"] == "question_answers:qn_213"
    answered_changed = _chunk_changed_frames(hub, since=before2)
    assert answered_changed[-1]["cause"] == "question-answered"
    assert answered_changed[-1]["key"] == "question_answers:qn_213"


def test_edited_cause_carries_no_key(tmp_path: Path) -> None:
    """``edited`` has no fact table (Phase 1's documented exclusion) — the frame must
    not synthesize a fake key (issue #213)."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _BUILD_DELIVER_YAML}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    before = _latest_event_id(hub)
    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"default_effort": "high"})
    assert resp.status_code == 202, resp.text
    frames = _chunk_changed_frames(hub, since=before)
    assert frames[-1]["cause"] == "edited"
    assert "key" not in frames[-1]


def test_event_logged_carries_event_log_key(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "w1"}).status_code == 201
    before = _latest_event_id(hub)
    resp = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "event.recorded",
                    "payload": {"severity": "warning", "kind": "spend-ceiling", "message": "80% of budget"},
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    frames = _frames_of(hub, EVENT_LOGGED, since=before)
    assert len(frames) == 1
    assert frames[0]["key"].startswith("event_log:")


def test_registered_and_heartbeat_runner_changed_carry_no_key(tmp_path: Path) -> None:
    """No fact table backs `registered`/`heartbeat` — muted client-side, and the key
    must be genuinely absent rather than `null` (issue #213)."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "w1"}).status_code == 201
    assert hub.client.post("/api/fleet/runners/r1/heartbeats").status_code == 204
    frames = _frames_of(hub, RUNNER_CHANGED)
    assert [f["kind"] for f in frames] == ["registered", "heartbeat"]
    for frame in frames:
        assert "key" not in frame


def test_queue_changed_frame_carries_no_key(tmp_path: Path) -> None:
    """A reorder writes N rows with no per-row news — explicitly excluded (issue #213)."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _BUILD_DELIVER_YAML}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    before = _latest_event_id(hub)
    resp = hub.client.post(f"/api/chunks/{chunk_id}/promote")
    assert resp.status_code == 202, resp.text
    frames = _frames_of(hub, QUEUE_CHANGED, since=before)
    assert len(frames) == 1
    assert "key" not in frames[0]


def test_migrated_cause_carries_chunk_migrations_key(tmp_path: Path) -> None:
    """A fresh cross-graph migration (issue #90) — the ``migrated`` cause's own
    ``chunk_migrations`` key, distinct from ``node-completed``'s ``transitions`` key."""
    hub = build_hub(tmp_path)
    src_yaml = """
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
          to: graph:triage
"""
    target_yaml = """
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
"""
    assert hub.client.post("/api/graphs", json={"definition_yaml": target_yaml}).status_code == 201
    chunk_id, node_id = _claimed(hub, graph_yaml=src_yaml)
    before = _latest_event_id(hub)
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "migrate", "epoch": 1, "runner_id": "r1", "from_node_id": node_id, "artifacts": []},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "migrated"
    frames = _chunk_changed_frames(hub, since=before)
    assert frames[-1]["cause"] == "migrated"
    assert frames[-1]["key"].startswith("chunk_migrations:")
