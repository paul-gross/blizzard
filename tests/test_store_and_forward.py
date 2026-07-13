"""Store-and-forward idempotency at the hub (D-069/D-090, component tier).

The runner→hub push is store-and-forward always: every fact rides the outbound buffer
with a per-runner monotonic seq, and a replay — after a lost ack or an outage backlog
drain — must apply **exactly once**. The runner-side buffering-through-an-outage is
covered at the unit tier (``tests/test_runner_loop.py``); this file proves the hub's
half against the real app: ``POST /events`` re-acks an already-applied seq without
re-applying it (the per-runner high-water mark), and a re-submitted completion returns
its original outcome without a second transition or a second land (D-090).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import FakeForge, build_hub, report_lease

pytestmark = pytest.mark.component

_POINTER = {"provider": "github", "url": "http://forge.local/repos/acme/widget/issues/7"}

# A build -> deliver graph named `default-delivery`, reused by name on ingest (D-081),
# so a build completion reaches the deliver hub node in one pass — decoupled from the
# packaged default graph's build -> review -> deliver shape.
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
    mode: merge-to-main
"""


def _claim(hub) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    assert hub.client.post("/api/graphs", json={"definition_yaml": _BUILD_DELIVER_YAML}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"pointers": [_POINTER]}).json()["chunk_id"]
    node_id = hub.client.post(
        "/api/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    return chunk_id, node_id


def _completion(node_id: str, *, epoch: int) -> dict:
    return {
        "choice": "pass",
        "epoch": epoch,
        "runner_id": "r1",
        "from_node_id": node_id,
        "artifacts": [
            {"name": "w", "kind": "git_commit", "repo": "acme/widget", "branch_name": "b", "commit_hash": "c"}
        ],
    }


def test_events_reack_is_idempotent_by_seq_high_water(tmp_path: Path) -> None:
    """A pushed seq ≤ the runner's high-water mark is already-applied, never re-applied."""
    hub = build_hub(tmp_path)
    chunk_id, _ = _claim(hub)

    first = report_lease(hub, chunk_id, epoch=1, seq=1)
    assert first["applied"] == [1] and first["already_applied"] == []
    assert first["high_water"] == 1

    # The replay: the exact same seq is re-acked as already-applied — the mark does not
    # move and no second lease fact lands (the chunk's latest epoch stays 1).
    replay = report_lease(hub, chunk_id, epoch=1, seq=1)
    assert replay["applied"] == [] and replay["already_applied"] == [1]
    assert replay["high_water"] == 1
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["latest_epoch"] == 1

    # A fresh seq advances the mark; then both are already-applied on the next drain.
    second = report_lease(hub, chunk_id, epoch=2, seq=2)
    assert second["applied"] == [2] and second["high_water"] == 2
    redrain = hub.client.post(
        "/api/events",
        json={
            "runner_id": "r1",
            "facts": [
                {"seq": 1, "kind": "lease.minted", "payload": {"chunk_id": chunk_id, "epoch": 1}},
                {"seq": 2, "kind": "lease.minted", "payload": {"chunk_id": chunk_id, "epoch": 2}},
            ],
        },
    ).json()
    assert redrain["applied"] == [] and sorted(redrain["already_applied"]) == [1, 2]
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["latest_epoch"] == 2


def test_reflushed_completion_applies_exactly_once(tmp_path: Path) -> None:
    """A re-submitted completion (lost-ack replay) lands once — one transition, one merge."""
    forge = FakeForge()
    hub = build_hub(tmp_path, forge=forge)
    chunk_id, build_node_id = _claim(hub)
    report_lease(hub, chunk_id, epoch=1, seq=1)

    first = hub.client.post(f"/api/chunks/{chunk_id}/completions", json=_completion(build_node_id, epoch=1))
    assert first.json()["outcome"] == "hub_node_taken"
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"
    assert [r.commit_hash for r in forge.landed] == ["c"]

    # The runner's flush ack was lost, so it re-submits the very same completion. The
    # hub returns the original outcome from the idempotency probe (D-090) — no second
    # transition, and the merge queue does not run again (the forge sees no new land).
    replay = hub.client.post(f"/api/chunks/{chunk_id}/completions", json=_completion(build_node_id, epoch=1))
    assert replay.json()["outcome"] == "hub_node_taken"
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"
    assert [r.commit_hash for r in forge.landed] == ["c"]  # still exactly one land


def test_escalation_fact_rides_events_and_derives_needs_human(tmp_path: Path) -> None:
    """The other buffered hub fact: escalation.recorded lands via /events, dedup and all."""
    hub = build_hub(tmp_path)
    chunk_id, _ = _claim(hub)
    report_lease(hub, chunk_id, epoch=1, seq=1)

    push = hub.client.post(
        "/api/events",
        json={
            "runner_id": "r1",
            "facts": [{"seq": 2, "kind": "escalation.recorded", "payload": {"chunk_id": chunk_id, "epoch": 1}}],
        },
    ).json()
    assert push["applied"] == [2]
    # An open escalation with no later lease mint derives needs_human (domain/events.md).
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "needs_human"

    replay = hub.client.post(
        "/api/events",
        json={
            "runner_id": "r1",
            "facts": [{"seq": 2, "kind": "escalation.recorded", "payload": {"chunk_id": chunk_id, "epoch": 1}}],
        },
    ).json()
    assert replay["already_applied"] == [2]  # dedup — the escalation is not doubled
