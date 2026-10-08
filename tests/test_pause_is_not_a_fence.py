"""A chunk pause is a brake on the runner, not a fence on the hub's write paths (component tier).

A completion, a cross-graph migration through one, and a runner-config gate decision each land
on a chunk carrying a pause, driven through the fleet routes over a real store."""

from __future__ import annotations

from pathlib import Path

import pytest

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
          to: review
        hand-off:
          description: Hand off to the other graph.
          to: graph:other
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
"""

_OTHER_YAML = """
name: other
entry: start
nodes:
  start:
    executor: runner
    prompt: |
      Start over.
    judgement:
      prompt: |
        Assess.
      choices:
        pass:
          description: Done.
          to: done
"""


class _PausedChunk:
    """A claimed, leased chunk standing at ``build`` with the operator's pause set on it."""

    def __init__(self, hub: HubHarness) -> None:
        self.hub = hub
        graph = hub.client.post("/api/graphs", json={"definition_yaml": _YAML})
        assert graph.status_code == 201, graph.text
        other = hub.client.post("/api/graphs", json={"definition_yaml": _OTHER_YAML})
        assert other.status_code == 201, other.text
        self.nodes = {n["name"]: n["node_id"] for n in graph.json()["nodes"]}
        self.chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
        claim = hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": self.chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
        )
        assert claim.status_code == 201, claim.text
        report_lease(hub, self.chunk_id, epoch=1, seq=1)
        assert hub.client.post(f"/api/fleet/chunks/{self.chunk_id}/pause").status_code == 202
        assert self.detail()["pause"] is not None

    def complete(self, choice: str) -> dict:
        resp = self.hub.client.post(
            f"/api/fleet/chunks/{self.chunk_id}/completions",
            json={
                "choice": choice,
                "epoch": 1,
                "runner_id": "r1",
                "from_node_id": self.nodes["build"],
                "artifacts": [],
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def decide(self) -> dict:
        resp = self.hub.client.post(
            f"/api/fleet/chunks/{self.chunk_id}/decisions",
            json={"from_node_id": self.nodes["build"], "epoch": 1, "runner_id": "r1", "artifacts": []},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def detail(self) -> dict:
        return self.hub.client.get(f"/api/chunks/{self.chunk_id}").json()


def test_a_completion_lands_on_a_paused_chunk(tmp_path: Path) -> None:
    chunk = _PausedChunk(build_hub(tmp_path))

    result = chunk.complete("pass")

    assert result["outcome"] != "failure", result
    assert chunk_rows(chunk.hub, s.transitions, chunk.chunk_id) == 1
    assert chunk.detail()["current_node_id"] == chunk.nodes["review"]


def test_a_migration_through_a_completion_lands_on_a_paused_chunk(tmp_path: Path) -> None:
    chunk = _PausedChunk(build_hub(tmp_path))

    result = chunk.complete("hand-off")

    assert result["outcome"] != "failure", result
    assert chunk_rows(chunk.hub, s.chunk_migrations, chunk.chunk_id) == 1


def test_a_runner_config_gate_decision_lands_on_a_paused_chunk(tmp_path: Path) -> None:
    chunk = _PausedChunk(build_hub(tmp_path))

    assert chunk.decide()["outcome"] == "parked_at_gate"

    assert chunk_rows(chunk.hub, s.decisions, chunk.chunk_id) == 1
