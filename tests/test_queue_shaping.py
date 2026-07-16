"""Queue shaping — ready-queue reorder and grouping (D-048/D-076/D-047), component tier.

Drives the real hub over a tmp store: ``GET /queue/peek`` honours the explicit order,
``POST /queue/reorder`` moves a ready chunk, and ``POST /chunks/{id}/group`` merges
unacquired chunks into one surviving chunk. Ordering and grouping are fact-derived
(D-004), so every assertion reads the derived surface, never a stored column.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from tests.support import HubHarness, build_hub, pointer_token

pytestmark = pytest.mark.component


def _ingest(hub: HubHarness, n: int) -> str:
    """Ingest and promote one chunk holding a distinct pointer, advancing the clock.

    Queue shaping is ready-only, so the chunk is promoted out of its not-ready resting
    state (D-103) before it can be peeked, reordered, or grouped."""
    pointer = {"source": "default", "ref": str(n)}
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token(pointer)]})
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    hub.clock.advance(timedelta(seconds=1))  # distinct minted_at → deterministic FIFO
    return chunk_id


def _peek_ids(hub: HubHarness) -> list[str]:
    resp = hub.client.get("/api/queue/peek")
    assert resp.status_code == 200, resp.text
    return [e["chunk_id"] for e in resp.json()["entries"]]


def test_peek_is_fifo_by_mint_before_any_reorder(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b, c = _ingest(hub, 1), _ingest(hub, 2), _ingest(hub, 3)
    assert _peek_ids(hub) == [a, b, c]


def test_reorder_to_top_and_to_a_position(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b, c = _ingest(hub, 1), _ingest(hub, 2), _ingest(hub, 3)

    # Move c to the top.
    resp = hub.client.post("/api/queue/reorder", json={"chunk_id": c, "position": 0})
    assert resp.status_code == 200, resp.text
    assert [e["chunk_id"] for e in resp.json()["entries"]] == [c, a, b]
    assert _peek_ids(hub) == [c, a, b]  # the peek honours it (D-048)

    # Move a to the middle (index 1 of the current [c, a, b] with a removed → [c, b]).
    hub.client.post("/api/queue/reorder", json={"chunk_id": a, "position": 1})
    assert _peek_ids(hub) == [c, a, b]

    # Move c to the bottom.
    hub.client.post("/api/queue/reorder", json={"chunk_id": c, "position": 99})
    assert _peek_ids(hub) == [a, b, c]


def test_reorder_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/queue/reorder", json={"chunk_id": "ch_nope", "position": 0})
    assert resp.status_code == 404


def test_reorder_non_ready_chunk_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a = _ingest(hub, 1)
    # Claim it so it derives running, not ready.
    claim = hub.client.post(
        "/api/routes",
        json={"chunk_id": a, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    resp = hub.client.post("/api/queue/reorder", json={"chunk_id": a, "position": 0})
    assert resp.status_code == 409
    assert "not ready" in resp.json()["detail"]


def test_group_merges_pointers_and_discards_the_rest(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    survivor, b, c = _ingest(hub, 1), _ingest(hub, 2), _ingest(hub, 3)

    resp = hub.client.post(f"/api/chunks/{survivor}/group", json={"merge_chunk_ids": [b, c]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["chunk_id"] == survivor
    assert sorted(body["merged_chunk_ids"]) == sorted([b, c])
    # The survivor carries the union of all three pointers (D-076).
    refs = {p["ref"] for p in body["pm_pointers"]}
    assert refs == {"1", "2", "3"}
    # The merged-away chunks are ephemeral — gone from the queue and the fleet list (D-047).
    assert _peek_ids(hub) == [survivor]
    listed = {row["chunk_id"] for row in hub.client.get("/api/chunks").json()}
    assert listed == {survivor}
    assert hub.client.get(f"/api/chunks/{b}").status_code == 404


def test_group_is_pointer_union_deduped(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    shared = {"source": "default", "ref": "shared"}
    survivor = hub.client.post("/api/chunks", json={"tokens": [pointer_token(shared)]}).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{survivor}/promote").status_code == 202  # ready to group (D-103)
    hub.clock.advance(timedelta(seconds=1))
    # A second chunk cannot re-ingest the same live pointer (D-093), so give it its own.
    other = _ingest(hub, 9)
    resp = hub.client.post(f"/api/chunks/{survivor}/group", json={"merge_chunk_ids": [survivor, other]})
    assert resp.status_code == 200, resp.text
    # Self-reference in merge_chunk_ids is a no-op; the union has no duplicate.
    refs = [p["ref"] for p in resp.json()["pm_pointers"]]
    assert sorted(refs) == ["9", "shared"]


def test_group_rejects_a_non_ready_member(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    survivor, running = _ingest(hub, 1), _ingest(hub, 2)
    hub.client.post(
        "/api/routes",
        json={"chunk_id": running, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    resp = hub.client.post(f"/api/chunks/{survivor}/group", json={"merge_chunk_ids": [running]})
    assert resp.status_code == 409
    # Nothing was merged — the running chunk is untouched, the survivor keeps one pointer.
    assert hub.client.get(f"/api/chunks/{running}").status_code == 200
    survivor_detail = hub.client.get(f"/api/chunks/{survivor}").json()
    assert len(survivor_detail["pm_pointers"]) == 1


def test_group_into_unknown_survivor_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    b = _ingest(hub, 1)
    resp = hub.client.post("/api/chunks/ch_nope/group", json={"merge_chunk_ids": [b]})
    assert resp.status_code == 404


def test_grouped_pointer_reingest_points_at_survivor(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    survivor, b = _ingest(hub, 1), _ingest(hub, 2)
    hub.client.post(f"/api/chunks/{survivor}/group", json={"merge_chunk_ids": [b]})
    # b's pointer now lives on the survivor (a live chunk), so re-ingesting it is a 409
    # naming the survivor, not the discarded chunk (D-093/D-047).
    resp = hub.client.post(
        "/api/chunks",
        json={"tokens": [pointer_token({"source": "default", "ref": "2"})]},
    )
    assert resp.status_code == 409
    assert resp.json()["existing_chunk_id"] == survivor
