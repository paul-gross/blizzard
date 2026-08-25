"""``GET``/``PUT /api/queue`` and ``GET``/``PUT /api/backlog`` — the ready-queue and
``not_ready``-list reads and whole-order replaces (#104, ``bzh:ranking-is-per-list``).

``GET /api/queue`` is the hub-ordered ready-queue view; ``PUT /api/queue`` is the
idempotent whole-order replacement (``bzh:domain-takes-objects``): the controller
resolves every id before the domain ever sees a ``Chunk``. ``GET``/``PUT /api/backlog``
are the ``not_ready`` list's own counterparts, ranked independently — never mixed with
the ready queue's order. A runner bearer token is rejected on every route."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from blizzard.auth_core import Role
from blizzard.hub.store import schema as s
from tests.support import HubHarness, build_hub, pointer_token, seed_session, seed_user

pytestmark = pytest.mark.component


def _ingest(hub: HubHarness, n: int) -> str:
    pointer = {"source": "default", "ref": str(n)}
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token(pointer)]})
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    hub.clock.advance(timedelta(seconds=1))  # distinct minted_at → deterministic FIFO
    return chunk_id


def _ingest_backlog(hub: HubHarness, n: int) -> str:
    """Ingest one chunk holding a distinct pointer and leave it ``not_ready``."""
    pointer = {"source": "default", "ref": str(n)}
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token(pointer)]})
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]
    hub.clock.advance(timedelta(seconds=1))  # distinct minted_at → deterministic FIFO
    return chunk_id


def _ids(entries: list[dict]) -> list[str]:
    return [e["chunk_id"] for e in entries]


# --- GET /api/queue ---------------------------------------------------------


def test_get_queue_returns_the_ordered_ready_view(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b, c = _ingest(hub, 1), _ingest(hub, 2), _ingest(hub, 3)
    resp = hub.client.get("/api/queue")
    assert resp.status_code == 200, resp.text
    assert _ids(resp.json()["entries"]) == [a, b, c]


def test_a_chunk_minted_long_ago_but_promoted_last_still_sorts_last(tmp_path: Path) -> None:
    # Issue #137: an un-moved chunk's fallback sort key is its promotion instant, not
    # its mint instant, so a late-promoted chunk sits at the tail, not mid-queue.
    hub = build_hub(tmp_path)
    old_pointer = {"source": "default", "ref": "old"}
    old = hub.client.post("/api/chunks", json={"tokens": [pointer_token(old_pointer)]}).json()["chunk_id"]
    hub.clock.advance(timedelta(days=30))  # old was minted long before anything else

    a, b = _ingest(hub, 1), _ingest(hub, 2)  # minted and promoted well after `old`
    assert hub.client.post(f"/api/chunks/{old}/promote").status_code == 202  # promoted last

    assert _ids(hub.client.get("/api/queue").json()["entries"]) == [a, b, old]


def test_re_promoting_an_already_ready_chunk_does_not_move_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b, c = _ingest(hub, 1), _ingest(hub, 2), _ingest(hub, 3)

    # Re-promote the head of the queue — a double board click, a CLI retry — and confirm
    # it stays exactly where it was rather than being shoved to the tail.
    assert hub.client.post(f"/api/chunks/{a}/promote").status_code == 202
    assert _ids(hub.client.get("/api/queue").json()["entries"]) == [a, b, c]


# --- PUT /api/queue — whole-order replace -----------------------------------


def test_put_queue_replaces_the_whole_order(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b, c = _ingest(hub, 1), _ingest(hub, 2), _ingest(hub, 3)
    resp = hub.client.put("/api/queue", json={"chunk_ids": [c, a, b]})
    assert resp.status_code == 200, resp.text
    assert _ids(resp.json()["entries"]) == [c, a, b]
    assert _ids(hub.client.get("/api/queue").json()["entries"]) == [c, a, b]


def test_put_queue_is_idempotent(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b, c = _ingest(hub, 1), _ingest(hub, 2), _ingest(hub, 3)
    order = {"chunk_ids": [b, c, a]}
    first = hub.client.put("/api/queue", json=order)
    second = hub.client.put("/api/queue", json=order)
    assert first.json() == second.json()
    assert _ids(second.json()["entries"]) == [b, c, a]


def test_put_queue_appends_unlisted_ready_chunks_after_the_named_ones(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b, c = _ingest(hub, 1), _ingest(hub, 2), _ingest(hub, 3)
    # Only name b — a and c are unlisted and keep their relative FIFO order at the tail.
    resp = hub.client.put("/api/queue", json={"chunk_ids": [b]})
    assert resp.status_code == 200, resp.text
    assert _ids(resp.json()["entries"]) == [b, a, c]


def test_put_queue_naming_a_non_ready_chunk_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b = _ingest(hub, 1), _ingest(hub, 2)
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": a, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    resp = hub.client.put("/api/queue", json={"chunk_ids": [a, b]})
    assert resp.status_code == 409
    assert a in resp.json()["detail"]
    # Rejected wholesale — b's ready order is untouched by the failed attempt.
    assert _ids(hub.client.get("/api/queue").json()["entries"]) == [b]


def test_put_queue_naming_an_unknown_chunk_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _ingest(hub, 1)
    resp = hub.client.put("/api/queue", json={"chunk_ids": ["ch_nope"]})
    assert resp.status_code == 409
    assert "ch_nope" in resp.json()["detail"]


def test_put_queue_duplicate_ids_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a = _ingest(hub, 1)
    resp = hub.client.put("/api/queue", json={"chunk_ids": [a, a]})
    assert resp.status_code == 422


# --- POST /api/queue/position — single-chunk fractional reorder (issue #137) -----


def _position_row_count(hub: HubHarness) -> int:
    with hub.engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(s.queue_positions)).scalar_one()


def test_post_queue_position_inserts_between_two_neighbours(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b, c = _ingest(hub, 1), _ingest(hub, 2), _ingest(hub, 3)
    before = _position_row_count(hub)

    resp = hub.client.post("/api/queue/position", json={"chunk_id": c, "after_chunk_id": a})

    assert resp.status_code == 200, resp.text
    assert _ids(resp.json()["entries"]) == [a, c, b]
    assert _ids(hub.client.get("/api/queue").json()["entries"]) == [a, c, b]
    assert _position_row_count(hub) == before + 1


def test_post_queue_position_with_null_after_moves_to_the_top(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b, c = _ingest(hub, 1), _ingest(hub, 2), _ingest(hub, 3)
    before = _position_row_count(hub)

    resp = hub.client.post("/api/queue/position", json={"chunk_id": c, "after_chunk_id": None})

    assert resp.status_code == 200, resp.text
    assert _ids(resp.json()["entries"]) == [c, a, b]
    assert _position_row_count(hub) == before + 1


def test_post_queue_position_after_the_last_chunk_moves_to_the_bottom(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b, c = _ingest(hub, 1), _ingest(hub, 2), _ingest(hub, 3)
    before = _position_row_count(hub)

    resp = hub.client.post("/api/queue/position", json={"chunk_id": a, "after_chunk_id": c})

    assert resp.status_code == 200, resp.text
    assert _ids(resp.json()["entries"]) == [b, c, a]
    assert _position_row_count(hub) == before + 1


def test_post_queue_position_naming_a_non_ready_chunk_id_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b = _ingest(hub, 1), _ingest(hub, 2)
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": a, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    resp = hub.client.post("/api/queue/position", json={"chunk_id": a, "after_chunk_id": b})
    assert resp.status_code == 409
    assert a in resp.json()["detail"]


def test_post_queue_position_naming_a_non_ready_after_chunk_id_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b = _ingest(hub, 1), _ingest(hub, 2)
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": b, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    resp = hub.client.post("/api/queue/position", json={"chunk_id": a, "after_chunk_id": b})
    assert resp.status_code == 409
    assert b in resp.json()["detail"]


def test_post_queue_position_self_anchor_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a = _ingest(hub, 1)
    resp = hub.client.post("/api/queue/position", json={"chunk_id": a, "after_chunk_id": a})
    assert resp.status_code == 422


# --- GET/PUT /api/backlog, POST /api/backlog/position — the not_ready list's own
# reordering surface, ranked independently of the ready queue (``bzh:ranking-is-per-list``)
# ------------------------------------------------------------------------------------


def test_get_backlog_returns_the_ordered_not_ready_view(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b, c = _ingest_backlog(hub, 1), _ingest_backlog(hub, 2), _ingest_backlog(hub, 3)
    resp = hub.client.get("/api/backlog")
    assert resp.status_code == 200, resp.text
    assert _ids(resp.json()["entries"]) == [a, b, c]


def test_put_backlog_replaces_the_whole_order(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b, c = _ingest_backlog(hub, 1), _ingest_backlog(hub, 2), _ingest_backlog(hub, 3)
    resp = hub.client.put("/api/backlog", json={"chunk_ids": [c, a, b]})
    assert resp.status_code == 200, resp.text
    assert _ids(resp.json()["entries"]) == [c, a, b]
    assert _ids(hub.client.get("/api/backlog").json()["entries"]) == [c, a, b]


def test_put_backlog_appends_unlisted_not_ready_chunks_after_the_named_ones(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b, c = _ingest_backlog(hub, 1), _ingest_backlog(hub, 2), _ingest_backlog(hub, 3)
    resp = hub.client.put("/api/backlog", json={"chunk_ids": [b]})
    assert resp.status_code == 200, resp.text
    assert _ids(resp.json()["entries"]) == [b, a, c]


def test_put_backlog_duplicate_ids_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a = _ingest_backlog(hub, 1)
    resp = hub.client.put("/api/backlog", json={"chunk_ids": [a, a]})
    assert resp.status_code == 422


def test_post_backlog_position_inserts_between_two_neighbours(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b, c = _ingest_backlog(hub, 1), _ingest_backlog(hub, 2), _ingest_backlog(hub, 3)
    resp = hub.client.post("/api/backlog/position", json={"chunk_id": c, "after_chunk_id": a})
    assert resp.status_code == 200, resp.text
    assert _ids(resp.json()["entries"]) == [a, c, b]
    assert _ids(hub.client.get("/api/backlog").json()["entries"]) == [a, c, b]


def test_post_backlog_position_self_anchor_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a = _ingest_backlog(hub, 1)
    resp = hub.client.post("/api/backlog/position", json={"chunk_id": a, "after_chunk_id": a})
    assert resp.status_code == 422


# --- Cross-list refusal — each route resolves candidates against its own list only
# (``bzh:ranking-is-per-list``); the refusal message names both lists -----------------


def test_put_queue_naming_a_not_ready_chunk_is_409_naming_both_lists(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ready = _ingest(hub, 1)
    backlog = _ingest_backlog(hub, 2)
    resp = hub.client.put("/api/queue", json={"chunk_ids": [ready, backlog]})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert backlog in detail
    assert "ready" in detail
    assert "not_ready" in detail


def test_put_backlog_naming_a_ready_chunk_is_409_naming_both_lists(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    backlog = _ingest_backlog(hub, 1)
    ready = _ingest(hub, 2)
    resp = hub.client.put("/api/backlog", json={"chunk_ids": [backlog, ready]})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert ready in detail
    assert "ready" in detail
    assert "not_ready" in detail


def test_post_backlog_position_naming_a_ready_chunk_id_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    backlog = _ingest_backlog(hub, 1)
    ready = _ingest(hub, 2)
    resp = hub.client.post("/api/backlog/position", json={"chunk_id": ready, "after_chunk_id": backlog})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert ready in detail
    assert "not_ready" in detail
    assert "ready" in detail


def test_post_queue_position_naming_a_not_ready_after_chunk_id_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ready = _ingest(hub, 1)
    backlog = _ingest_backlog(hub, 2)
    resp = hub.client.post("/api/queue/position", json={"chunk_id": ready, "after_chunk_id": backlog})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert backlog in detail
    assert "ready" in detail
    assert "not_ready" in detail


# --- Backlog permission — QUEUE_REORDER even to read, unlike the ready queue's
# FLEET_VIEW read (``bzh:ranking-is-per-list``: the backlog rank is an operator triage
# surface, not fleet-wide visibility) --------------------------------------------------


def test_a_fleet_view_only_principal_is_refused_the_backlog_read(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    guest = seed_user(hub, username="reader", role=Role.GUEST)
    token = seed_session(hub, guest)
    resp = hub.client.get("/api/backlog", headers={"Cookie": f"bz_session={token}"})
    assert resp.status_code == 403
    # The same principal reads the ready queue fine — FLEET_VIEW is enough there.
    assert hub.client.get("/api/queue", headers={"Cookie": f"bz_session={token}"}).status_code == 200


# --- Promotion still lands a backlog-ranked chunk at the ready tail (issue #137) —
# now that queue_positions() carries both lists' facts -------------------------------


def test_promoting_a_backlog_reordered_chunk_still_lands_it_at_the_ready_tail(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a, b = _ingest(hub, 1), _ingest(hub, 2)  # already ready, ahead of everything below
    x, y = _ingest_backlog(hub, 3), _ingest_backlog(hub, 4)

    # Rank y ahead of x in the backlog — an explicit, small position fact — before
    # either is promoted.
    resp = hub.client.post("/api/backlog/position", json={"chunk_id": y, "after_chunk_id": None})
    assert resp.status_code == 200, resp.text
    assert _ids(hub.client.get("/api/backlog").json()["entries"]) == [y, x]

    # y's backlog rank must not leak into the ready queue: promoting it lands it at the
    # tail, behind the already-ready a/b, not ahead of them by its small backlog position.
    assert hub.client.post(f"/api/chunks/{y}/promote").status_code == 202
    assert _ids(hub.client.get("/api/queue").json()["entries"]) == [a, b, y]


# --- Runner principal is still rejected on every route in this router -------


def test_runner_bearer_token_is_rejected_on_get_and_put_queue(tmp_path: Path) -> None:
    from blizzard.hub.config import RUNNER_AUTH_ENFORCE
    from tests.test_fleet_auth import _bearer, _seed_enrolled

    token = _seed_enrolled(tmp_path)
    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)
    assert hub.client.get("/api/queue", headers=_bearer(token)).status_code == 403
    assert hub.client.put("/api/queue", json={"chunk_ids": []}, headers=_bearer(token)).status_code == 403
    assert hub.client.get("/api/backlog", headers=_bearer(token)).status_code == 403
    assert hub.client.put("/api/backlog", json={"chunk_ids": []}, headers=_bearer(token)).status_code == 403
