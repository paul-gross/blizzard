"""Completion apply — the epoch fence, terminal rejection, and bad choices (component tier).

The happy path and idempotent replay are covered by ``tests/component/hub/test_command_node.py``; this file
pins the rejection edges (``bzh:facts-not-status``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from blizzard.foundation.node_steps import ApplyOutcome
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.hub.node_steps import completion_submission
from blizzard.runner.leases.model import NewLease
from blizzard.runner.loop.steps import Advance, Pull
from blizzard.runner.node_steps.submissions import ApplyReply
from tests.support.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeWorktreeGit,
    make_context,
    make_envelope,
    make_store,
)
from tests.support.runner_mint import new_mint
from tests.support.scaffolding import HubHarness, build_hub, make_ready, pointer_token, report_lease

_POINTER = {"source": "default", "ref": "9"}

# A build -> deliver graph named `default-delivery`, reused by name on ingest, so these
# apply-mechanics tests reach a terminal chunk in one build pass.
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
    action: pass
    judgement:
      choices:
        success:
          description: Delivered.
          to: done
        failure:
          description: Failed to deliver.
          to: build
"""


# A spike -> deliver graph: one read-only work node that `produces` an asset and no
# code, routing into the same hub deliver node a code chunk uses.
_SPIKE_DELIVER_YAML = """
name: default-delivery
entry: spike
nodes:
  spike:
    executor: runner
    prompt: |
      Investigate; write nothing.
    produces:
      - spike-notes
    judgement:
      prompt: |
        Record the finding.
      choices:
        complete:
          description: Investigation done; findings recorded.
          to: deliver
  deliver:
    executor: hub
    action: pass
    judgement:
      choices:
        success:
          description: Delivered.
          to: done
        failure:
          description: Failed to deliver.
          to: spike
"""


def _claimed(hub: HubHarness, *, graph_yaml: str = _BUILD_DELIVER_YAML) -> tuple[str, str]:
    """Mint the graph, claim a route, and report the runner-minted lease (epoch 1)."""
    assert hub.client.post("/api/graphs", json={"definition_yaml": graph_yaml}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    make_ready(hub, chunk_id)
    node_id = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)  # the fence input the completion checks against
    return chunk_id, node_id


def _completion(node_id: str, *, epoch: int, choice: str = "pass") -> dict[str, Any]:
    return {
        "choice": choice,
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


def test_stale_epoch_is_rejected_and_nothing_lands(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claimed(hub)

    completion = {**_completion(node_id, epoch=99), "reason": "Stale work must not land."}
    resp = hub.client.post(f"/api/fleet/chunks/{chunk_id}/completions", json=completion)
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "failure"
    chunk = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert chunk["status"] == "running"
    assert chunk["history"] == []
    assert not any(a["kind"] == "git_commit" for a in chunk["artifacts"])


def test_accepted_reason_survives_replay_with_different_prose(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claimed(hub)
    completion: dict[str, Any] = {**_completion(node_id, epoch=1), "reason": "Checks passed; ready to deliver."}

    first = hub.client.post(f"/api/fleet/chunks/{chunk_id}/completions", json=completion)
    assert first.status_code == 200
    assert first.json()["outcome"] != "failure"
    replay = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions", json={**completion, "reason": "Different replay prose."}
    )
    assert replay.status_code == 200
    assert replay.json() == first.json()

    chunk = hub.client.get(f"/api/chunks/{chunk_id}").json()
    transitions = [t for t in chunk["history"] if t["from_node_id"] == node_id]
    assert len(transitions) == 1
    assert transitions[0]["reason"] == completion["reason"]
    artifacts = [a for a in chunk["artifacts"] if a["kind"] == "git_commit"]
    assert len(artifacts) == 1
    assert artifacts[0]["commit_hash"] == completion["artifacts"][0]["commit_hash"]


@pytest.mark.parametrize(
    ("reason", "truncated", "expected"),
    [
        ("a" * 1999 + " ", False, "a" * 1999),
        ("a" * 1999 + " ", True, "a" * 1999 + " "),
        (" \n ", True, None),
        ("  " + "🍦" * 1996 + "  ", True, "🍦" * 1996),
    ],
    ids=["raw-boundary", "clipped-boundary", "blank", "invalid-clipped"],
)
def test_hub_normalizes_untrusted_completion_reason(
    tmp_path: Path, reason: str, truncated: bool, expected: str | None
) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claimed(hub)
    response = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={**_completion(node_id, epoch=1), "reason": reason, "reason_truncated": truncated},
    )
    assert response.status_code == 200
    assert response.json()["outcome"] != "failure"
    history = hub.client.get(f"/api/chunks/{chunk_id}").json()["history"]
    assert next(row for row in history if row["from_node_id"] == node_id)["reason"] == expected


def test_runner_judgement_boundary_reason_persists_at_hub(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claimed(hub)
    now = datetime(2026, 7, 13, 12, tzinfo=UTC)
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    store.record_mint(
        new_mint(
            NewLease(
                lease_id="lease_r",
                chunk_id=chunk_id,
                graph_id="gr_1",
                node_id=node_id,
                node_name="build",
                epoch=1,
                retries_max=2,
                created_at=now,
            )
        )
    )
    store.record_spawn(
        "lease_r",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=now,
    )
    store.record_binding(chunk_id=chunk_id, environment_id="e1", workdir="/ws/e1", bound_at=now, workspace_id="ws1")
    runner_hub = FakeHub()
    runner_hub.envelopes[chunk_id] = make_envelope(
        chunk_id, "build", node_id=node_id, choices=[("pass", "meets criteria")], produces=["findings"]
    )
    runner_hub.apply_responses = [ApplyReply(outcome=ApplyOutcome.NEXT, next_envelope=runner_hub.envelopes[chunk_id])]
    assessment = "a" * 1999 + " b"
    ctx = make_context(
        store,
        hub=runner_hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(
            handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100),
            verdict="pass",
            assessment=assessment,
        ),
        probe=FakeProbe(),
        worktree_git=FakeWorktreeGit(),
    )
    Advance(ctx).run()
    Advance(ctx).run()
    Advance(ctx).run()
    Pull(ctx).run()
    _, submission = runner_hub.completions[0]
    payload = completion_submission(submission).model_dump(mode="json")
    # The hub graph requires its git artifact; retain the runner's submitted assessment.
    payload["artifacts"] = _completion(node_id, epoch=1)["artifacts"]
    response = hub.client.post(f"/api/fleet/chunks/{chunk_id}/completions", json=payload)
    assert response.status_code == 200
    assert response.json()["outcome"] != "failure"
    history = hub.client.get(f"/api/chunks/{chunk_id}").json()["history"]
    assert next(row for row in history if row["from_node_id"] == node_id)["reason"] == "a" * 1999 + " "


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"commit_hash": "abc123"}, "commit_hash"),
        ({"branch_name": "a:b"}, "branch_name"),
        ({"repo": ""}, "repo"),
    ],
)
def test_a_malformed_commit_pointer_is_refused_with_its_detail_and_nothing_lands(
    overrides: dict[str, str], field: str, tmp_path: Path
) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claimed(hub)
    completion = _completion(node_id, epoch=1)
    completion["artifacts"][0].update(overrides)

    resp = hub.client.post(f"/api/fleet/chunks/{chunk_id}/completions", json=completion)

    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "failure"
    assert "git_commit artifact `w`" in body["detail"] and f"`{field}`" in body["detail"]
    chunk = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert chunk["status"] == "running"
    assert not any(a["kind"] == "git_commit" for a in chunk["artifacts"])


def test_completion_on_terminal_chunk_fails(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claimed(hub)
    hub.client.post(f"/api/fleet/chunks/{chunk_id}/completions", json=_completion(node_id, epoch=1))
    hub.settle_turns()
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"

    # A fresh (non-replayed) completion on a done chunk is rejected as terminal. A
    # later epoch keeps it out of the idempotency replay path so the terminal guard fires.
    late = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions", json=_completion(node_id, epoch=2, choice="fail")
    )
    assert late.json()["outcome"] == "failure"


def test_unknown_choice_is_a_failure(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claimed(hub)
    completion = {**_completion(node_id, epoch=1, choice="nope"), "reason": "This choice is invalid."}
    resp = hub.client.post(f"/api/fleet/chunks/{chunk_id}/completions", json=completion)
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "failure"
    chunk = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert chunk["status"] == "running"
    assert chunk["history"] == []
    assert not any(a["kind"] == "git_commit" for a in chunk["artifacts"])


def test_non_code_chunk_completes_with_only_asset_artifacts(tmp_path: Path) -> None:
    """A spike chunk carrying only an asset reaches ``done`` — no code lands (criterion
    10, 2nd sentence)."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claimed(hub, graph_yaml=_SPIKE_DELIVER_YAML)

    completion = {
        "choice": "complete",
        "epoch": 1,
        "runner_id": "r1",
        "from_node_id": node_id,
        "artifacts": [{"name": "spike-notes", "kind": "asset", "content": "no change warranted"}],
    }
    resp = hub.client.post(f"/api/fleet/chunks/{chunk_id}/completions", json=completion)
    assert resp.status_code == 200
    hub.settle_turns()

    # Fleet truth: the empty deliver still finalizes the chunk terminal.
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"
    # Hub-durable artifacts: no git-commit pointer ever lands — only the spike's own
    # asset.
    artifacts = hub.client.get(f"/api/chunks/{chunk_id}").json()["artifacts"]
    assert {a["kind"] for a in artifacts} == {"asset"}, f"expected only asset artifacts, got: {artifacts}"
    spike_notes = next(a for a in artifacts if a["name"] == "spike-notes")
    assert spike_notes["content"] == "no change warranted"


def test_completion_on_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post(
        "/api/fleet/chunks/ch_missing/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": "nd_x"},
    )
    assert resp.status_code == 404


# A build -> deliver graph whose `build` gates its `pass` choice on green checks (#114).
_CHECKS_GATED_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    checks:
      - mise run test
    judgement:
      prompt: |
        Assess the build.
      choices:
        pass:
          description: Complete and green.
          to: deliver
          requires_checks: true
        fail:
          description: Incomplete.
          to: build
  deliver:
    executor: hub
    action: pass
    judgement:
      choices:
        success:
          description: Delivered.
          to: done
        failure:
          description: Failed to deliver.
          to: build
"""


def _completion_with_checks(
    node_id: str, *, choice: str, check_results: list[dict[str, Any]], epoch: int = 1
) -> dict[str, Any]:
    return {
        "choice": choice,
        "epoch": epoch,
        "runner_id": "r1",
        "from_node_id": node_id,
        "check_results": check_results,
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


def test_checks_gate_backstop_fences_a_red_gated_pass(tmp_path: Path) -> None:
    """The hub backstop rejects a ``requires_checks`` pass whose reported checks are red —
    the chunk never advances (AC #4, hub side)."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claimed(hub, graph_yaml=_CHECKS_GATED_YAML)

    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json=_completion_with_checks(
            node_id, choice="pass", check_results=[{"command": "mise run test", "passed": False}]
        ),
    )
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "failure"
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "running"


def test_checks_gate_backstop_admits_a_green_gated_pass(tmp_path: Path) -> None:
    """A ``requires_checks`` pass with green checks passes the backstop and delivers."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claimed(hub, graph_yaml=_CHECKS_GATED_YAML)

    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json=_completion_with_checks(
            node_id, choice="pass", check_results=[{"command": "mise run test", "passed": True}]
        ),
    )
    assert resp.status_code == 200
    assert resp.json()["outcome"] != "failure"
    hub.settle_turns()
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"


def test_checks_gate_backstop_lets_a_red_check_through_a_non_gated_fail(tmp_path: Path) -> None:
    """A red check reported through the non-gated ``fail`` choice routes normally — back to
    build, not a gate failure (AC #5, hub side)."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _claimed(hub, graph_yaml=_CHECKS_GATED_YAML)

    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json=_completion_with_checks(
            node_id, choice="fail", check_results=[{"command": "mise run test", "passed": False}]
        ),
    )
    assert resp.status_code == 200
    assert resp.json()["outcome"] != "failure"  # a normal transition back to build
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "running"
