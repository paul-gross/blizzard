"""Store-and-forward idempotency at the hub (component tier).

Every fact rides the outbound buffer with a per-runner monotonic seq, and a replay must
apply exactly once: ``POST /events`` re-acks an already-applied seq without re-applying
it, and a re-submitted completion returns its original outcome without a second land.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.hub.internal.http_hub import HttpHubClient
from blizzard.runner.hub.outbound import COMPLETION_KIND
from blizzard.runner.lifecycle.drain import OutboundDrain
from blizzard.runner.loop.context import LoopConfig
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    make_context,
    make_store,
    registered_identity,
)
from tests.support import FakeHubCommandRunner, FakeHubWorkdir, build_hub, make_ready, pointer_token, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "7"}

# A build -> deliver graph named `default-delivery`, reused by name on ingest, so a
# build completion reaches the deliver hub node in one pass.
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


def _claim(hub) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    assert hub.client.post("/api/graphs", json={"definition_yaml": _BUILD_DELIVER_YAML}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    make_ready(hub, chunk_id)
    node_id = hub.client.post(
        "/api/fleet/routes",
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
            {
                "name": "w",
                "kind": "git_commit",
                "repo": "acme/widget",
                "branch_name": "b",
                "commit_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            }
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
        "/api/fleet/events",
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
    hub = build_hub(tmp_path)
    chunk_id, build_node_id = _claim(hub)
    report_lease(hub, chunk_id, epoch=1, seq=1)

    first = hub.client.post(f"/api/fleet/chunks/{chunk_id}/completions", json=_completion(build_node_id, epoch=1))
    assert first.json()["outcome"] == "hub_node_taken"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "done"
    assert len([t for t in detail["history"] if t["from_node_name"] == "build"]) == 1

    # The runner's flush ack was lost, so it re-submits the same completion. The hub
    # returns the original outcome — no second transition, no re-run.
    replay = hub.client.post(f"/api/fleet/chunks/{chunk_id}/completions", json=_completion(build_node_id, epoch=1))
    assert replay.json()["outcome"] == "hub_node_taken"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "done"
    assert len([t for t in detail["history"] if t["from_node_name"] == "build"]) == 1  # still exactly one transition


def test_escalation_fact_rides_events_and_derives_needs_human(tmp_path: Path) -> None:
    """The other buffered hub fact: escalation.recorded lands via /events, dedup and
    all, carrying ``wrapped_takeover_command`` through the round trip."""
    hub = build_hub(tmp_path)
    chunk_id, _ = _claim(hub)
    report_lease(hub, chunk_id, epoch=1, seq=1)

    takeover = "cd /ws/e1 && mock-claude-code --resume sess-abc"
    # `--dir` names the runner's own runtime root — one flat path shared across every
    # chunk it escalates, not a per-chunk path.
    wrapped = f"blizzard runner takeover {chunk_id} --dir /runner/data/runtime"
    push = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 2,
                    "kind": "escalation.recorded",
                    "payload": {
                        "chunk_id": chunk_id,
                        "epoch": 1,
                        "takeover_command": takeover,
                        "wrapped_takeover_command": wrapped,
                    },
                }
            ],
        },
    ).json()
    assert push["applied"] == [2]
    # An open escalation with no later lease mint derives needs_human.
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "needs_human"
    assert detail["escalation"]["takeover_command"] == takeover
    assert detail["escalation"]["wrapped_takeover_command"] == wrapped

    replay = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [{"seq": 2, "kind": "escalation.recorded", "payload": {"chunk_id": chunk_id, "epoch": 1}}],
        },
    ).json()
    assert replay["already_applied"] == [2]  # dedup — the escalation is not doubled


def test_escalation_fact_without_wrapped_takeover_reads_back_empty(tmp_path: Path) -> None:
    """An older runner that never learned to compose ``wrapped_takeover_command``
    omits it; the field reads back empty while ``takeover_command`` lands."""
    hub = build_hub(tmp_path)
    chunk_id, _ = _claim(hub)
    report_lease(hub, chunk_id, epoch=1, seq=1)

    takeover = "cd /ws/e1 && mock-claude-code --resume sess-abc"
    push = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 2,
                    "kind": "escalation.recorded",
                    "payload": {"chunk_id": chunk_id, "epoch": 1, "takeover_command": takeover},
                }
            ],
        },
    ).json()
    assert push["applied"] == [2]
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["escalation"]["takeover_command"] == takeover
    assert detail["escalation"]["wrapped_takeover_command"] == ""


def test_replay_and_hub_advance_during_a_slow_hub_node_start_no_second_run(tmp_path: Path) -> None:
    """The first completion's answer is withheld while its hub node runs: the runner's re-submit
    answers ``hub_node_taken`` and ``hub-advance`` answers ``ran=false`` — neither starts a command —
    and the node's exit lands exactly once."""
    runner = FakeHubCommandRunner()
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=FakeHubWorkdir())
    chunk_id, build_node_id = _claim(hub)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    entered = threading.Event()
    release = threading.Event()

    def before_run(_command: str) -> None:
        entered.set()
        release.wait(timeout=5)

    runner.before_run = before_run
    first: dict = {}
    thread = threading.Thread(
        target=lambda: first.update(
            r=hub.client.post(f"/api/fleet/chunks/{chunk_id}/completions", json=_completion(build_node_id, epoch=1))
        )
    )
    thread.start()
    assert entered.wait(timeout=5), "the hub node never started"

    replay = hub.client.post(f"/api/fleet/chunks/{chunk_id}/completions", json=_completion(build_node_id, epoch=1))
    assert replay.json()["outcome"] == "hub_node_taken"
    advance = hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")
    assert advance.json()["ran"] is False
    assert len(runner.calls) == 1

    release.set()
    thread.join(timeout=5)
    assert first["r"].json()["outcome"] == "hub_node_taken"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "done"
    assert len([t for t in detail["history"] if t["from_node_name"] == "deliver"]) == 1
    assert len([t for t in detail["history"] if t["from_node_name"] == "build"]) == 1

    late = hub.client.post(f"/api/fleet/chunks/{chunk_id}/completions", json=_completion(build_node_id, epoch=1))
    assert late.json()["outcome"] == "hub_node_taken"
    assert len(runner.calls) == 1


def test_runner_drain_acks_a_replayed_completion_during_a_slow_hub_node_and_flushes_the_facts_behind_it(
    tmp_path: Path,
) -> None:
    """The runner's buffered completion is re-submitted while its hub node is still running: the
    drain acks the ``hub_node_taken`` answer and drains the generic fact queued behind it, before
    the node finishes."""
    runner = FakeHubCommandRunner()
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=FakeHubWorkdir())
    chunk_id, build_node_id = _claim(hub)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    entered = threading.Event()
    release = threading.Event()

    def before_run(_command: str) -> None:
        entered.set()
        release.wait(timeout=5)

    runner.before_run = before_run
    thread = threading.Thread(
        target=lambda: hub.client.post(
            f"/api/fleet/chunks/{chunk_id}/completions", json=_completion(build_node_id, epoch=1)
        )
    )
    thread.start()
    assert entered.wait(timeout=5), "the hub node never started"

    now = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
    store = make_store("sqlite://")
    harness = FakeHarness(handle=WorkerHandle(session_id="s", pid=1, process_start_time="1", pgid=1), verdict=None)
    ctx = make_context(
        store,
        hub=cast(FakeHub, HttpHubClient(hub.client)),
        provider=FakeProvider({"e": "/ws/e"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_name="r1", workspace_id="w1"),
        clock=FixedClock(now),
        registered=False,
    )
    # The real hub client carries no fake's default id: hold the id its fleet client's token resolves to.
    identity = registered_identity("r1", "r1").current()
    assert identity is not None
    store.record_runner_identity(identity)
    ctx.identity.hold(identity)
    submission = {**_completion(build_node_id, epoch=1), "check_results": [], "proposals": []}
    ctx.stores.outbound.enqueue_outbound(
        kind=COMPLETION_KIND,
        chunk_id=chunk_id,
        lease_id="lease_gone",
        payload=json.dumps({"submission": submission}),
        created_at=now,
    )
    ctx.stores.outbound.enqueue_outbound(
        kind="lease.minted",
        chunk_id=chunk_id,
        lease_id=None,
        payload=json.dumps({"chunk_id": chunk_id, "epoch": 1}),
        created_at=now,
    )

    OutboundDrain(ctx).run()

    assert ctx.stores.outbound.pending_outbound() == []
    assert len(runner.calls) == 1

    release.set()
    thread.join(timeout=5)
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert len([t for t in detail["history"] if t["from_node_name"] == "deliver"]) == 1
