"""Helpers for the analytics durations api tests, shared with other test modules."""

from __future__ import annotations

from pathlib import Path

from blizzard.auth_core import Role
from tests.support.hub_request_auth import cookie
from tests.support.scaffolding import (
    FakeWorkSource,
    HubHarness,
    build_hub,
    claim_route,
    pointer_token,
    seed_session,
    seed_user,
)

#: Named to match the packaged default graph's own name, so `POST /api/chunks` mints
#: onto *this* graph — `GraphMintService.ensure_default` is idempotent by name.
_GRAPH_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: Build it.
    judgement:
      prompt: Assess the build.
      choices:
        pass:
          description: Complete.
          to: review
        fail:
          description: Incomplete.
          to: build
  review:
    executor: runner
    prompt: Review it.
    judgement:
      prompt: Assess the review.
      choices:
        pass:
          description: Complete.
          to: done
"""


def seeded_hub(
    tmp_path: Path,
    *,
    work_sources: dict[str, FakeWorkSource] | None = None,
    graph_yaml: str = _GRAPH_YAML,
):
    hub = build_hub(
        tmp_path,
        auth_mode="oauth",
        work_sources=work_sources,
    )
    admin = seed_user(hub, username="admin", role=Role.ADMIN)
    admin_token = seed_session(hub, admin)
    graph = hub.client.post("/api/graphs", json={"definition_yaml": graph_yaml}, headers=cookie(admin_token))
    assert graph.status_code == 201, graph.text
    graph_id = graph.json()["graph_id"]
    nodes = {n["name"]: n["node_id"] for n in graph.json()["nodes"]}

    contributor = seed_user(hub, username="ada", role=Role.CONTRIBUTOR)
    token = seed_session(hub, contributor)
    return hub, token, graph_id, nodes


def mint_chunk(hub: HubHarness, token: str, *, source: str = "default", ref: str = "1") -> str:
    resp = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": source, "ref": ref})]}, headers=cookie(token)
    )
    assert resp.status_code == 201, resp.text
    chunk_id = str(resp.json()["chunk_id"])
    claim_route(hub, chunk_id)
    return chunk_id


def complete_node(hub: HubHarness, chunk_id: str, *, epoch: int, from_node_id: str, choice: str) -> None:
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": choice, "epoch": epoch, "runner_id": "r1", "from_node_id": from_node_id},
    )
    assert resp.status_code == 200, resp.text
