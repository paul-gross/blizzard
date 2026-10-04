"""A route left on a terminal chunk confers no tenure: the operator's detach refuses it — there
is no claim to release — and so does the route-token rekey; neither writes anything."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub, pointer_token, report_lease

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
"""


def _claimed_chunk(hub) -> str:  # type: ignore[no-untyped-def]
    assert hub.client.post("/api/fleet/runners", json={"runner_id": "runner-a", "workspace_id": "ws-a"}).is_success
    assert hub.client.post("/api/graphs", json={"definition_yaml": _YAML}).status_code == 201
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "664"})]}
    ).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "runner-a", "workspace_id": "ws-a", "environment_ids": ["e1"]},
    )
    assert claim.status_code == 201, claim.text
    return str(chunk_id)


def _done_chunk(hub) -> str:  # type: ignore[no-untyped-def]
    chunk_id = _claimed_chunk(hub)
    node_id = hub.client.get(f"/api/chunks/{chunk_id}").json()["current_node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1, runner_id="runner-a")
    done = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "runner-a", "from_node_id": node_id, "artifacts": []},
    )
    assert done.status_code == 200 and done.json()["outcome"] == "done", done.text
    return chunk_id


def test_detach_of_a_done_chunk_still_carrying_its_finishers_route_is_refused(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _done_chunk(hub)
    route = hub.services.chunks.route.route_of(chunk_id)
    assert route is not None

    resp = hub.client.post(f"/api/chunks/{chunk_id}/detach")

    assert resp.status_code == 409, resp.text
    assert "holds no claim" in resp.json()["detail"]
    assert hub.services.chunks.route.route_of(chunk_id) == route
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"


def test_detach_of_a_running_chunk_still_releases_its_route(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _claimed_chunk(hub)

    resp = hub.client.post(f"/api/chunks/{chunk_id}/detach")

    assert resp.status_code == 202, resp.text
    assert hub.services.chunks.route.route_of(chunk_id) is None


def test_rekey_of_a_done_chunk_still_carrying_its_finishers_route_is_refused(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _done_chunk(hub)
    assert hub.services.chunks.route.route_of(chunk_id) is not None

    resp = hub.client.post(f"/api/fleet/chunks/{chunk_id}/route-token")

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == f"chunk {chunk_id} is done, its route confers no tenure"
