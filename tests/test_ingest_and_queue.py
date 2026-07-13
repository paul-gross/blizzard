"""Ingest, the live-pointer conflict (D-093), and the ready-queue peek (component tier)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from tests.support import build_hub

pytestmark = pytest.mark.component

_P1 = {"provider": "github", "url": "http://forge.local/repos/acme/widget/issues/1"}
_P2 = {"provider": "github", "url": "http://forge.local/repos/acme/widget/issues/2"}


def test_ingest_mints_a_chunk_pinned_to_the_default_graph(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/chunks", json={"pointers": [_P1]})
    assert resp.status_code == 201
    chunk_id = resp.json()["chunk_id"]
    assert chunk_id.startswith("ch_")

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "ready"
    assert detail["pm_pointers"] == [_P1]
    # The default graph was minted on first ingest and the chunk pinned to it.
    graphs = hub.services.graphs.list_all()
    assert [g.name for g in graphs] == ["default-delivery"]
    assert detail["graph_id"] == graphs[0].graph_id


def test_ingest_batches_multiple_pointers_into_one_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/chunks", json={"pointers": [_P1, _P2]})
    assert resp.status_code == 201
    detail = hub.client.get(f"/api/chunks/{resp.json()['chunk_id']}").json()
    assert detail["pm_pointers"] == [_P1, _P2]


def test_live_pointer_reingest_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = hub.client.post("/api/chunks", json={"pointers": [_P1]}).json()["chunk_id"]

    conflict = hub.client.post("/api/chunks", json={"pointers": [_P1]})
    assert conflict.status_code == 409
    body = conflict.json()
    assert body["existing_chunk_id"] == first
    assert body["url"] == _P1["url"]


def test_terminal_pointer_reingest_mints_a_fresh_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = hub.client.post("/api/chunks", json={"pointers": [_P1]}).json()["chunk_id"]
    # Drive the chunk terminal by claiming + delivering.
    node_id = hub.client.post(
        "/api/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": 1,
            "runner_id": "r1",
            "from_node_id": node_id,
            "artifacts": [
                {"name": "w", "kind": "git_commit", "repo": "acme/widget", "branch_name": "b", "commit_hash": "c"}
            ],
        },
    )
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"

    # Re-ingesting the same pointer once every prior holder is terminal is legal (D-093).
    again = hub.client.post("/api/chunks", json={"pointers": [_P1]})
    assert again.status_code == 201
    assert again.json()["chunk_id"] != chunk_id


def test_queue_peek_lists_ready_chunks_fifo_and_hides_claimed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = hub.client.post("/api/chunks", json={"pointers": [_P1]}).json()["chunk_id"]
    hub.clock.advance(timedelta(seconds=1))  # a distinct, later mint time for FIFO ordering
    second = hub.client.post("/api/chunks", json={"pointers": [_P2]}).json()["chunk_id"]

    entries = hub.client.get("/api/queue/peek").json()["entries"]
    assert [e["chunk_id"] for e in entries] == [first, second]
    assert [e["position"] for e in entries] == [0, 1]

    # Claiming the first removes it from the ready queue.
    hub.client.post(
        "/api/routes",
        json={"chunk_id": first, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    remaining = hub.client.get("/api/queue/peek").json()["entries"]
    assert [e["chunk_id"] for e in remaining] == [second]
