"""Ingest, the live-pointer conflict (D-093), and the ready-queue peek (component tier)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from tests.support import build_hub, ingest

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
    assert detail["status"] == "not_ready"  # rests not-ready until promoted (D-103)
    assert detail["pm_pointers"] == [{**_P1, "label": "gh:widget#1"}]
    # The default graph was minted on first ingest and the chunk pinned to it.
    graphs = hub.services.graphs.list_all()
    assert [g.name for g in graphs] == ["default-delivery"]
    assert detail["graph_id"] == graphs[0].graph_id


def test_ingest_batches_multiple_pointers_into_one_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/chunks", json={"pointers": [_P1, _P2]})
    assert resp.status_code == 201
    detail = hub.client.get(f"/api/chunks/{resp.json()['chunk_id']}").json()
    assert detail["pm_pointers"] == [
        {**_P1, "label": "gh:widget#1"},
        {**_P2, "label": "gh:widget#2"},
    ]


def test_list_row_is_board_legible(tmp_path: Path) -> None:
    # The fleet list resolves the current node's human name and each pointer's
    # `{code}:{repo}#{number}` label server-side, so the board renders `build` and
    # `gh:widget#1` without reassembly (D-075). A non-issue-shaped URL degrades to a
    # null label rather than erroring.
    hub = build_hub(tmp_path)
    chunk_id = hub.client.post("/api/chunks", json={"pointers": [_P1]}).json()["chunk_id"]
    opaque = {"provider": "github", "url": "http://forge.local/acme/widget/wiki"}
    opaque_id = hub.client.post("/api/chunks", json={"pointers": [opaque]}).json()["chunk_id"]

    rows = {r["chunk_id"]: r for r in hub.client.get("/api/chunks").json()}
    assert rows[chunk_id]["current_node_name"] == "build"  # the entry node, pre-first-transition
    assert rows[chunk_id]["pm_pointers"] == [{**_P1, "label": "gh:widget#1"}]
    assert rows[opaque_id]["pm_pointers"] == [{**opaque, "label": None}]


def test_ingest_rests_not_ready_and_promote_makes_it_claimable(tmp_path: Path) -> None:
    # Ingest mints not-ready (D-103): visible on the fleet list, absent from the ready queue,
    # so no runner claims it. Promoting flips it to ready and admits it to the queue.
    hub = build_hub(tmp_path)
    chunk_id = hub.client.post("/api/chunks", json={"pointers": [_P1]}).json()["chunk_id"]
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "not_ready"
    assert [r["chunk_id"] for r in hub.client.get("/api/chunks").json()] == [chunk_id]  # on the board
    assert hub.client.get("/api/queue/peek").json()["entries"] == []  # never claimed

    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    assert [e["chunk_id"] for e in hub.client.get("/api/queue/peek").json()["entries"]] == [chunk_id]


def test_promote_is_idempotent_and_404s_unknown_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = hub.client.post("/api/chunks", json={"pointers": [_P1]}).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    # A second promote is a harmless no-op — still ready, still one queue entry.
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    assert len(hub.client.get("/api/queue/peek").json()["entries"]) == 1
    assert hub.client.post("/api/chunks/ch_nope/promote").status_code == 404


def test_live_pointer_reingest_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = hub.client.post("/api/chunks", json={"pointers": [_P1]}).json()["chunk_id"]

    conflict = hub.client.post("/api/chunks", json={"pointers": [_P1]})
    assert conflict.status_code == 409
    body = conflict.json()
    assert body["existing_chunk_id"] == first
    assert body["url"] == _P1["url"]


def _pass(hub, chunk_id: str, node_id: str, epoch: int, *, artifacts: list[dict]) -> dict:  # type: ignore[no-untyped-def]
    return hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": epoch,
            "runner_id": "r1",
            "from_node_id": node_id,
            "artifacts": artifacts,
        },
    ).json()


def test_terminal_pointer_reingest_mints_a_fresh_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = hub.client.post("/api/chunks", json={"pointers": [_P1]}).json()["chunk_id"]
    # Drive the chunk terminal through the default build -> review -> deliver graph.
    build_id = hub.client.post(
        "/api/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    commit = [{"name": "w", "kind": "git_commit", "repo": "acme/widget", "branch_name": "b", "commit_hash": "c"}]
    to_review = _pass(hub, chunk_id, build_id, 1, artifacts=commit)
    review_id = to_review["next_envelope"]["node"]["node_id"]
    # Report the review node-step's fresh lease so the hub's fence tracks it (D-044).
    assert hub.client.post(f"/api/chunks/{chunk_id}/leases", json={"epoch": 2, "runner_id": "r1"}).status_code == 202
    _pass(hub, chunk_id, review_id, 2, artifacts=[])
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"

    # Re-ingesting the same pointer once every prior holder is terminal is legal (D-093).
    again = hub.client.post("/api/chunks", json={"pointers": [_P1]})
    assert again.status_code == 201
    assert again.json()["chunk_id"] != chunk_id


def test_queue_peek_lists_ready_chunks_fifo_and_hides_claimed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = ingest(hub, [_P1])  # ingest + promote → ready and in the queue (D-103)
    hub.clock.advance(timedelta(seconds=1))  # a distinct, later mint time for FIFO ordering
    second = ingest(hub, [_P2])

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
