"""Closed steps for the trace component tests — chunks driven through the hub's real routes to a close."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.graph.model import Graph
from blizzard.hub.store import schema as s
from tests.support import HubHarness, build_hub, hub_scoped, ingest, report_lease

GRAPH_YAML = """
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
        fail:
          description: Incomplete.
          to: build
  review:
    executor: runner
    prompt: |
      Review the change.
    judgement:
      prompt: |
        Assess the review.
      choices:
        pass:
          description: Approved.
          to: done
        fail:
          description: Rejected.
          to: build
"""


def label(ref: WorkRef) -> str | None:
    return f"{ref.source}#{ref.ref}"


def trace_hub(tmp_path: Path, **build: Any) -> tuple[HubHarness, Graph]:
    hub = build_hub(tmp_path, **build)
    assert hub.client.post("/api/graphs", json={"definition_yaml": GRAPH_YAML}).status_code == 201
    graph = hub.services.graphs.get_enabled_by_name("default-delivery")
    assert graph is not None
    return hub, graph


def claim(hub: HubHarness, chunk_id: str, seq: int, *, runner_id: str = "r1") -> None:
    resp = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": runner_id, "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert resp.status_code == 201, resp.text
    report_lease(hub, chunk_id, epoch=1, seq=seq, runner_id=runner_id)


def pass_build(hub: HubHarness, chunk_id: str, graph: Graph, *, runner_id: str = "r1") -> None:
    build = next(n for n in graph.nodes if n.name == "build")
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": runner_id, "from_node_id": build.node_id, "artifacts": []},
    )
    assert resp.status_code == 200, resp.text


def stop(hub: HubHarness, chunk_id: str) -> None:
    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"}).status_code == 202


def transitioned_and_stopped(hub: HubHarness, graph: Graph, ref: int, *, runner_id: str = "r1") -> tuple[str, str]:
    """Two chunks claimed at the same instant by ``runner_id``: one transitions out of ``build``, one is stopped."""
    moved = ingest(hub, [{"source": "default", "ref": str(ref)}])
    stopped = ingest(hub, [{"source": "default", "ref": str(ref + 1)}])
    claim(hub, moved, seq=ref, runner_id=runner_id)
    claim(hub, stopped, seq=ref + 1, runner_id=runner_id)
    hub.clock.advance(timedelta(seconds=5))
    pass_build(hub, moved, graph, runner_id=runner_id)
    stop(hub, stopped)
    return moved, stopped


def seed_open_tenant_chunk(hub: HubHarness, tenant: str, chunk_id: str, at: datetime) -> None:
    """A second open tenant ``tenant`` whose chunk, on a graph of its own, closes a step one second
    after ``at`` with usage billed and a transcript segment dropped, then completes a second later."""

    def insert(table: sa.Table, **values: Any) -> None:
        with hub.engine.begin() as conn:
            conn.execute(hub_scoped(sa.insert(table).values(tenant_id=tenant, **values)))

    graph_id = f"gr_{tenant}"
    with hub.engine.begin() as conn:
        conn.execute(
            hub_scoped(sa.insert(s.tenants).values(tenant_id=tenant, name=tenant, created_at=at, created_by="test"))
        )
    insert(s.graphs, graph_id=graph_id, name="g", entry_node_id="nd_1", definition_yaml="", created_at=at)
    insert(s.chunks, chunk_id=chunk_id, graph_id=graph_id, minted_at=at, model="")
    insert(s.lease_facts, chunk_id=chunk_id, epoch=1, runner_id="r1", minted_at=at)
    insert(s.epoch_owners, chunk_id=chunk_id, epoch=1, runner_id="r1", recorded_at=at)
    step_closed = at + timedelta(seconds=1)
    insert(
        s.transitions,
        transition_id=f"tr_{tenant}",
        chunk_id=chunk_id,
        graph_id=graph_id,
        to_node_id="nd_2",
        epoch=1,
        runner_id="r1",
        recorded_at=step_closed,
    )
    insert(
        s.usage_facts,
        chunk_id=chunk_id,
        node_id="nd_1",
        epoch=1,
        runner_id="r1",
        kind="spawn",
        model="m",
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
        cache_create_tokens=0,
        recorded_at=step_closed,
    )
    insert(
        s.transcript_event_drops,
        segment_id=f"sg_{tenant}",
        chunk_id=chunk_id,
        epoch=1,
        spawn_generation=0,
        dropped_at=step_closed,
    )
    insert(s.chunk_completed, chunk_id=chunk_id, completed_at=at + timedelta(seconds=2), completed_by="test")
