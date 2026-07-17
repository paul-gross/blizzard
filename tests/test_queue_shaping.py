"""Queue shaping — ready-queue reorder and grouping, component tier.

Drives the real hub over a tmp store: ``GET /queue/peek`` honours the explicit order,
``POST /queue/reorder`` moves a ready chunk, and ``POST /chunks/{id}/group`` merges
unacquired chunks into one surviving chunk. Ordering and grouping are fact-derived,
so every assertion reads the derived surface, never a stored column.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from tests.support import HubHarness, build_hub, pointer_token, write_chunk_pause_facts

pytestmark = pytest.mark.component


def _ingest(hub: HubHarness, n: int) -> str:
    """Ingest and promote one chunk holding a distinct pointer, advancing the clock.

    Queue shaping is ready-only, so the chunk is promoted out of its not-ready resting
    state before it can be peeked, reordered, or grouped."""
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
    assert _peek_ids(hub) == [c, a, b]  # the peek honours it

    # Move a to the middle (index 1 of the current [c, a, b] with a removed → [c, b]).
    hub.client.post("/api/queue/reorder", json={"chunk_id": a, "position": 1})
    assert _peek_ids(hub) == [c, a, b]

    # Move c to the bottom.
    hub.client.post("/api/queue/reorder", json={"chunk_id": c, "position": 99})
    assert _peek_ids(hub) == [a, b, c]


def _pause(tmp_path: Path, hub: HubHarness, chunk_id: str) -> None:
    write_chunk_pause_facts(tmp_path, chunk_id, (True, hub.clock.now()))


def test_paused_ready_chunk_is_excluded_from_the_queue(tmp_path: Path) -> None:
    # The free win (issue #46 §4): list_ready()/peek filter on status is ChunkStatus.READY,
    # so a paused chunk drops out with no queue filter at all — pinned here as a property,
    # not an accident.
    hub = build_hub(tmp_path)
    a, b = _ingest(hub, 1), _ingest(hub, 2)
    _pause(tmp_path, hub, a)
    assert _peek_ids(hub) == [b]


def test_paused_chunk_with_a_live_route_is_still_excluded_from_the_queue(tmp_path: Path) -> None:
    # Paused wins over ``running`` in the derivation precedence too — a held chunk is
    # never READY regardless, but this pins that a live route doesn't smuggle it back in.
    hub = build_hub(tmp_path)
    a, b = _ingest(hub, 1), _ingest(hub, 2)
    claim = hub.client.post(
        "/api/routes",
        json={"chunk_id": a, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    _pause(tmp_path, hub, a)
    assert _peek_ids(hub) == [b]
    # Confirms this is the pause branch, not merely "running is already excluded":
    # paused wins over running in the derivation precedence (D-067/issue #46).
    assert hub.client.get(f"/api/chunks/{a}").json()["status"] == "paused"


# --- Newest-fact-wins across the store seam (issue #46) -----------------------
#
# ``_is_paused`` reads the pause list's tail, which only means "newest" because
# ``load_facts`` hydrates ``chunk_pause_facts`` ordered by ``id``. The unit tier proves the
# pure function over a hand-built list and structurally cannot see that ordering — reverse
# the hydration and every resume silently becomes a no-op, with the whole unit tier green.
# These pin the two halves together over multi-fact sequences the single-fact tests above
# cannot distinguish.


def test_pause_then_resume_returns_the_chunk_to_the_queue(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b = _ingest(hub, 1), _ingest(hub, 2)
    now = hub.clock.now()
    write_chunk_pause_facts(tmp_path, a, (True, now), (False, now + timedelta(seconds=1)))
    assert hub.client.get(f"/api/chunks/{a}").json()["status"] == "ready"
    assert _peek_ids(hub) == [a, b]  # back in the queue, in its original FIFO slot


def test_re_pause_after_resume_stays_out_of_the_queue(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b = _ingest(hub, 1), _ingest(hub, 2)
    now = hub.clock.now()
    write_chunk_pause_facts(
        tmp_path,
        a,
        (True, now),
        (False, now + timedelta(seconds=1)),
        (True, now + timedelta(seconds=2)),
    )
    assert hub.client.get(f"/api/chunks/{a}").json()["status"] == "paused"
    assert _peek_ids(hub) == [b]


def test_same_instant_pause_and_resume_resolve_by_write_order(tmp_path: Path) -> None:
    # Two facts can share a ``set_at`` — timestamps have finite granularity and a pause and
    # its resume can land inside one tick. Append-only write order (the ``id`` the hydration
    # orders by) is the tiebreak, not ``set_at``: the resume written second wins.
    hub = build_hub(tmp_path)
    a, b = _ingest(hub, 1), _ingest(hub, 2)
    now = hub.clock.now()
    write_chunk_pause_facts(tmp_path, a, (True, now), (False, now))
    assert hub.client.get(f"/api/chunks/{a}").json()["status"] == "ready"
    assert _peek_ids(hub) == [a, b]


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
    # The survivor carries the union of all three pointers.
    refs = {p["ref"] for p in body["pm_pointers"]}
    assert refs == {"1", "2", "3"}
    # The merged-away chunks are ephemeral — gone from the queue and the fleet list.
    assert _peek_ids(hub) == [survivor]
    listed = {row["chunk_id"] for row in hub.client.get("/api/chunks").json()}
    assert listed == {survivor}
    assert hub.client.get(f"/api/chunks/{b}").status_code == 404


def test_group_is_pointer_union_deduped(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    shared = {"source": "default", "ref": "shared"}
    survivor = hub.client.post("/api/chunks", json={"tokens": [pointer_token(shared)]}).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{survivor}/promote").status_code == 202  # ready to group
    hub.clock.advance(timedelta(seconds=1))
    # A second chunk cannot re-ingest the same live pointer, so give it its own.
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
    # naming the survivor, not the discarded chunk.
    resp = hub.client.post(
        "/api/chunks",
        json={"tokens": [pointer_token({"source": "default", "ref": "2"})]},
    )
    assert resp.status_code == 409
    assert resp.json()["existing_chunk_id"] == survivor
