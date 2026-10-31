"""The reserved ``escalate`` choice target, driven through the fleet routes (component tier).

A worker taking a choice whose ``to:`` is ``escalate`` parks the chunk ``needs_human`` with an
``escalate-choice`` escalation and records no transition. A repeat of the same choice is answered
as the escalation it is, any other verdict is refused, and a requeue re-runs the node."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import Table

from blizzard.hub.store import schema as s
from tests.support import HubHarness, build_hub, chunk_rows, ingest, report_lease

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
          to: done
        unfulfillable:
          description: A step no worker can complete.
          to: escalate
        blocked:
          description: Blocked on something outside the repository.
          to: escalate
"""

_ESCALATED = "escalated — requeue the chunk"
_PARKED = "parked_at_gate"


class _Chunk:
    def __init__(self, hub: HubHarness) -> None:
        self.hub = hub
        graph = hub.client.post("/api/graphs", json={"definition_yaml": _YAML})
        assert graph.status_code == 201, graph.text
        self.node_id = graph.json()["nodes"][0]["node_id"]
        self.chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
        claim = hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": self.chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
        )
        assert claim.status_code == 201, claim.text
        report_lease(hub, self.chunk_id, epoch=1, seq=1)

    def complete(self, choice: str, *, epoch: int = 1, decision_id: str | None = None) -> dict:
        body: dict = {
            "choice": choice,
            "epoch": epoch,
            "runner_id": "r1",
            "from_node_id": self.node_id,
            "artifacts": [],
        }
        if decision_id is not None:
            body["decision_id"] = decision_id
        resp = self.hub.client.post(f"/api/fleet/chunks/{self.chunk_id}/completions", json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()

    def detail(self) -> dict:
        return self.hub.client.get(f"/api/chunks/{self.chunk_id}").json()

    def rows(self, table: Table) -> int:
        return chunk_rows(self.hub, table, self.chunk_id)


def test_the_escalate_choice_parks_the_chunk_needs_human_and_records_no_transition(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))

    assert chunk.complete("unfulfillable")["outcome"] == _PARKED

    detail = chunk.detail()
    assert detail["status"] == "needs_human"
    assert detail["escalation"]["cause"] == "escalate-choice"
    assert "`build`" in detail["escalation"]["detail"] and "`unfulfillable`" in detail["escalation"]["detail"]
    assert detail["current_node_id"] == chunk.node_id
    assert chunk.rows(s.transitions) == 0 and chunk.rows(s.escalations) == 1


def test_resubmitting_the_escalate_choice_answers_as_escalated_and_writes_nothing(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.complete("unfulfillable")["outcome"] == _PARKED

    assert chunk.complete("unfulfillable")["outcome"] == _PARKED

    assert chunk.rows(s.escalations) == 1 and chunk.rows(s.transitions) == 0


def test_a_different_choice_after_the_escalate_choice_is_refused_and_leaves_it_open(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.complete("unfulfillable")["outcome"] == _PARKED

    refused = chunk.complete("pass")

    assert refused["outcome"] == "failure" and _ESCALATED in refused["detail"]
    assert chunk.rows(s.transitions) == 0
    assert chunk.detail()["status"] == "needs_human"


def test_a_gate_decision_resolved_to_the_escalate_choice_escalates_and_closes(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    decided = chunk.hub.client.post(
        f"/api/fleet/chunks/{chunk.chunk_id}/decisions",
        json={"from_node_id": chunk.node_id, "epoch": 1, "runner_id": "r1", "artifacts": []},
    )
    assert decided.status_code == 200, decided.text
    decision_id = chunk.detail()["decision"]["decision_id"]
    resolved = chunk.hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "unfulfillable"})
    assert resolved.status_code == 200, resolved.text

    assert chunk.complete("unfulfillable", decision_id=decision_id)["outcome"] == _PARKED

    detail = chunk.detail()
    assert detail["escalation"]["cause"] == "escalate-choice"
    assert detail.get("decision") is None
    assert chunk.rows(s.transitions) == 0


def test_a_requeue_supersedes_the_escalate_choice_and_re_runs_the_node(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.complete("unfulfillable")["outcome"] == _PARKED

    chunk.hub.clock.advance(timedelta(seconds=1))
    assert chunk.hub.client.post(f"/api/chunks/{chunk.chunk_id}/requeues").status_code == 202

    detail = chunk.detail()
    assert detail["status"] == "ready"
    assert detail["current_node_id"] == chunk.node_id
    assert detail.get("escalation") is None


def test_a_second_escalate_choice_after_the_first_is_refused_not_answered_as_its_replay(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.complete("unfulfillable")["outcome"] == _PARKED

    refused = chunk.complete("blocked")

    assert refused["outcome"] == "failure" and _ESCALATED in refused["detail"]
    assert chunk.rows(s.escalations) == 1
