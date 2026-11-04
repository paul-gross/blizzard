"""Helpers for the hub command node tests, shared with other test modules."""

from __future__ import annotations

from collections.abc import Mapping

import httpx2

from blizzard.hub.domain.graph.model import Graph
from tests.support import (
    HubHarness,
    pointer_token,
    report_lease,
)

_POINTER = {"source": "default", "ref": "42"}


HUB_CMD_GRAPH_YAML = """
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
          to: merge
        fail:
          description: Incomplete.
          to: build
  merge:
    executor: hub
    action: test.answer
    with: {choice: [success]}
    judgement:
      choices:
        success:
          description: Landed.
          to: done
        failure:
          description: Failed to land.
          to: build
"""


def to_merge_node(
    hub: HubHarness,
    pointer: Mapping[str, str] = _POINTER,
    graph_yaml: str = HUB_CMD_GRAPH_YAML,
    tokens: list[str] | None = None,
) -> tuple[str, str, Graph]:
    """Ingest, promote, claim, and complete ``build`` -> ``merge`` for one chunk.

    Returns ``(chunk_id, build_node_id, graph)``. The completion's own apply already
    runs the hub node executor synchronously (``src/blizzard/hub/domain/execution/apply.py``'s hub-node branch) — most
    tests below want that; the barrier test bypasses it (:func:`_park_at_merge`)."""
    assert hub.client.post("/api/graphs", json={"definition_yaml": graph_yaml}).status_code == 201
    resp = hub.client.post("/api/chunks", json={"tokens": tokens or [pointer_token(pointer)]})
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert claim.status_code == 201, claim.text
    build_node_id = claim.json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    graph = hub.services.graphs.get(chunk.graph_id)
    assert graph is not None
    return chunk_id, build_node_id, graph


def submit_build_pass(hub: HubHarness, chunk_id: str, build_node_id: str, epoch: int) -> httpx2.Response:
    """Complete ``build`` with ``pass``, then take every hub turn it leads to."""
    resp = post_build_pass(hub, chunk_id, build_node_id, epoch)
    hub.settle_turns()
    return resp


def post_build_pass(hub: HubHarness, chunk_id: str, build_node_id: str, epoch: int) -> httpx2.Response:
    return hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": epoch,
            "runner_id": "r1",
            "from_node_id": build_node_id,
            "check_results": [],
            "artifacts": [],
        },
    )


_POLL_GRAPH_YAML = """
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
          to: merge
        fail:
          description: Incomplete.
          to: build
  merge:
    executor: hub
    poll_interval: 30
    poll_timeout: 90
    action: test.sequence
    with: {choices: [pending, success]}
    judgement:
      choices:
        success:
          description: Green.
          to: done
        failure:
          description: Red.
          to: build
"""


#: ``merge`` answering ``pending`` on every pass.
ALWAYS_PENDING_GRAPH_YAML = _POLL_GRAPH_YAML.replace(
    "with: {choices: [pending, success]}", "with: {choices: [pending]}"
)
