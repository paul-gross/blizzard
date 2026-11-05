"""Helpers for the analytics spend api tests, shared with other test modules."""

from __future__ import annotations

from pathlib import Path

from blizzard.auth_core import Role
from tests.support.scaffolding import FakeWorkSource, HubHarness, build_hub, pointer_token, seed_session, seed_user

#: Named to match the packaged default graph's own name, so `POST /api/chunks` mints
#: onto *this* graph — see `tests/component/hub/api/test_analytics_durations.py`'s own note.
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


def _cookie(token: str) -> dict[str, str]:
    return {"Cookie": f"bz_session={token}"}


def seeded_hub(tmp_path: Path, *, work_sources: dict[str, FakeWorkSource] | None = None):
    hub = build_hub(tmp_path, auth_mode="oauth", work_sources=work_sources)
    admin = seed_user(hub, username="admin", role=Role.ADMIN)
    admin_token = seed_session(hub, admin)
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH_YAML}, headers=_cookie(admin_token))
    assert graph.status_code == 201, graph.text
    graph_id = graph.json()["graph_id"]
    nodes = {n["name"]: n["node_id"] for n in graph.json()["nodes"]}

    contributor = seed_user(hub, username="ada", role=Role.CONTRIBUTOR)
    token = seed_session(hub, contributor)
    return hub, token, graph_id, nodes


def mint_chunk(hub: HubHarness, token: str, *, source: str = "default", ref: str = "1") -> str:
    resp = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": source, "ref": ref})]}, headers=_cookie(token)
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["chunk_id"])


def push_usage(
    hub: HubHarness,
    *,
    chunk_id: str,
    node_id: str,
    epoch: int,
    seq: int,
    cost_usd: float | None,
    estimated_cost_usd: float | None = None,
) -> None:
    payload = {
        "chunk_id": chunk_id,
        "node_id": node_id,
        "epoch": epoch,
        "kind": "spawn",
        "model": "claude-opus-4-8",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 10,
        "cache_create_tokens": 5,
        "cost_usd": cost_usd,
    }
    if estimated_cost_usd is not None:
        payload["estimated_cost_usd"] = estimated_cost_usd
    resp = hub.client.post(
        "/api/fleet/events",
        json={"runner_id": "r1", "facts": [{"seq": seq, "kind": "usage.recorded", "payload": payload}]},
    )
    assert resp.status_code == 200, resp.text
