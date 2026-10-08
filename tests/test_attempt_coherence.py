"""The third guard on both runner write paths, driven through the fleet routes (component tier).

A completion and a runner-config gate decision are each refused when they come from a node the
chunk does not stand at, from a hub-executed node, or from an attempt whose own escalation or
question is open — answered ``failure`` with nothing written. The hub's own unresolvable-target
escalation lets through only a submission that would re-escalate, answered with no write."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import Table

from blizzard.hub.domain.chunk.ports.hub_exec import IWriteChunkHubExecRepository
from blizzard.hub.store import schema as s
from tests.support import (
    HubHarness,
    build_hub,
    chunk_rows,
    ingest,
    report_escalation,
    report_lease,
    report_question,
)

pytestmark = pytest.mark.component

_YAML = """
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
          to: review
        ship:
          description: Ready to deliver.
          to: deliver
        hand-off:
          description: Hand off to a graph nobody minted.
          to: graph:ghost
  review:
    executor: runner
    prompt: |
      Review the build.
    judgement:
      prompt: |
        Assess the review.
      choices:
        pass:
          description: Reviewed.
          to: done
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

_NOT_CURRENT = "is not the chunk's current node"
_HUB_EXECUTED = "is hub-executed"
_ESCALATED = "escalated — requeue the chunk"
_QUESTION_OPEN = "answer it before the chunk moves on"
# An unresolvable target answers `parked_at_gate`, never `failure`, which would requeue past it.
_ESCALATED_OUTCOME = "parked_at_gate"


class _Chunk:
    def __init__(self, hub: HubHarness) -> None:
        self.hub = hub
        graph = hub.client.post("/api/graphs", json={"definition_yaml": _YAML})
        assert graph.status_code == 201, graph.text
        self.nodes = {n["name"]: n["node_id"] for n in graph.json()["nodes"]}
        self.chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
        claim = hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": self.chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
        )
        assert claim.status_code == 201, claim.text
        self.seq = 1
        report_lease(hub, self.chunk_id, epoch=1, seq=self.seq)

    def complete(self, node: str, *, choice: str) -> dict:
        resp = self.hub.client.post(
            f"/api/fleet/chunks/{self.chunk_id}/completions",
            json={"choice": choice, "epoch": 1, "runner_id": "r1", "from_node_id": self.nodes[node], "artifacts": []},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def decide(self, node: str) -> dict:
        resp = self.hub.client.post(
            f"/api/fleet/chunks/{self.chunk_id}/decisions",
            json={"from_node_id": self.nodes[node], "epoch": 1, "runner_id": "r1", "artifacts": []},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def escalate(self) -> None:
        self.seq += 1
        assert report_escalation(self.hub, self.chunk_id, epoch=1, seq=self.seq)["applied"] == [self.seq]

    def ask(self) -> None:
        self.seq += 1
        assert report_question(self.hub, self.chunk_id, epoch=1, seq=self.seq)["applied"] == [self.seq]

    def stand_at_deliver(self) -> None:
        """Move onto the hub-executed ``deliver`` node with its run deferred — a live fleet-wide
        hub-exec slot holds it — so the chunk stands there."""
        hub_exec = cast(IWriteChunkHubExecRepository, self.hub.services.chunks.hub_exec)
        slot = hub_exec.acquire_hub_exec_slot(
            self.chunk_id, node_id="nd_elsewhere", at=self.hub.clock.now(), stale_after=timedelta(hours=1)
        )
        assert slot is not None
        assert self.complete("build", choice="ship")["outcome"] != "failure"
        assert self.detail()["current_node_id"] == self.nodes["deliver"]

    def detail(self) -> dict:
        return self.hub.client.get(f"/api/chunks/{self.chunk_id}").json()

    def rows(self, table: Table) -> int:
        return chunk_rows(self.hub, table, self.chunk_id)


def _refused(result: dict, detail: str) -> None:
    assert result["outcome"] == "failure", result
    assert detail in result["detail"], result


# --- Completion path ---------------------------------------------------------


def test_a_completion_from_a_node_the_chunk_does_not_stand_at_is_refused(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))

    _refused(chunk.complete("review", choice="pass"), _NOT_CURRENT)

    assert chunk.rows(s.transitions) == 0 and chunk.rows(s.chunk_migrations) == 0
    assert chunk.detail()["current_node_id"] == chunk.nodes["build"]


def test_a_completion_from_an_attempt_whose_escalation_is_open_is_refused(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    chunk.escalate()

    _refused(chunk.complete("build", choice="pass"), _ESCALATED)

    assert chunk.rows(s.transitions) == 0 and chunk.rows(s.chunk_migrations) == 0


def test_a_completion_from_an_attempt_whose_question_is_open_is_refused(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    chunk.ask()

    _refused(chunk.complete("build", choice="pass"), _QUESTION_OPEN)

    assert chunk.rows(s.transitions) == 0 and chunk.rows(s.chunk_migrations) == 0


def test_a_runner_completion_out_of_a_hub_executed_node_is_refused(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    chunk.stand_at_deliver()

    _refused(chunk.complete("deliver", choice="success"), _HUB_EXECUTED)

    assert chunk.rows(s.transitions) == 1  # only the arrival at `deliver`
    assert chunk.detail()["current_node_id"] == chunk.nodes["deliver"]


def test_a_completion_at_an_open_runner_config_gate_is_refused(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.decide("build")["outcome"] == "parked_at_gate"

    _refused(chunk.complete("build", choice="pass"), "is open at node `build`")

    assert chunk.rows(s.transitions) == 0 and chunk.rows(s.chunk_migrations) == 0


# --- Decision path -----------------------------------------------------------


def test_a_decision_from_a_node_the_chunk_does_not_stand_at_is_refused(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))

    _refused(chunk.decide("review"), _NOT_CURRENT)

    assert chunk.rows(s.decisions) == 0


def test_a_decision_from_an_attempt_whose_escalation_is_open_is_refused(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    chunk.escalate()

    _refused(chunk.decide("build"), _ESCALATED)

    assert chunk.rows(s.decisions) == 0


def test_a_decision_from_an_attempt_whose_question_is_open_is_refused(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    chunk.ask()

    _refused(chunk.decide("build"), _QUESTION_OPEN)

    assert chunk.rows(s.decisions) == 0


def test_a_decision_out_of_a_hub_executed_node_is_refused(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    chunk.stand_at_deliver()

    _refused(chunk.decide("deliver"), _HUB_EXECUTED)

    assert chunk.rows(s.decisions) == 0


def test_a_second_decision_at_an_open_gate_answers_as_its_replay_and_writes_no_second_row(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.decide("build")["outcome"] == "parked_at_gate"

    assert chunk.decide("build")["outcome"] == "parked_at_gate"

    assert chunk.rows(s.decisions) == 1


# --- The unresolvable-target escalation --------------------------------------


def test_resubmitting_the_unresolvable_choice_answers_as_escalated_and_writes_nothing(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.complete("build", choice="hand-off")["outcome"] == _ESCALATED_OUTCOME

    assert chunk.complete("build", choice="hand-off")["outcome"] == _ESCALATED_OUTCOME

    assert chunk.rows(s.escalations) == 1
    assert chunk.rows(s.transitions) == 0 and chunk.rows(s.chunk_migrations) == 0


def test_a_different_choice_after_the_unresolvable_escalation_is_refused_and_leaves_it_open(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.complete("build", choice="hand-off")["outcome"] == _ESCALATED_OUTCOME

    _refused(chunk.complete("build", choice="pass"), _ESCALATED)

    assert chunk.rows(s.transitions) == 0 and chunk.rows(s.chunk_migrations) == 0
    detail = chunk.detail()
    assert detail["status"] == "needs_human"
    assert detail["escalation"]["cause"] == "migration-target-unresolvable"
    assert detail["current_node_id"] == chunk.nodes["build"]


def test_a_decision_after_the_unresolvable_escalation_is_refused(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.complete("build", choice="hand-off")["outcome"] == _ESCALATED_OUTCOME

    _refused(chunk.decide("build"), _ESCALATED)

    assert chunk.rows(s.decisions) == 0
