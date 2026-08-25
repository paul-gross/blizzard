"""``GET /chunks/{id}/work-items`` renders a hub-owned pointer through the unchanged
handler (issue #357, component tier) — the built-in ``hub`` source needs no
``[[work_source]]`` to resolve at ingest or render at read.

Also exercises the source-addressed editor routes (blizzard#358): ``/api/work-sources``
and its ``{source}/items``/``{source}/items/{ref}`` children."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blizzard.auth_core import Role
from blizzard.hub.config import RUNNER_AUTH_ENFORCE
from blizzard.hub.events.broker import CHUNK_CHANGED, QUEUE_CHANGED
from tests.support import FakeWorkSource, build_hub, emitted_events, seed_session, seed_user

pytestmark = pytest.mark.component


def test_a_hub_owned_pointer_ingests_and_renders_its_title_and_body(tmp_path: Path) -> None:
    """Creation itself mints the item's chunk (blizzard#359) — no separate ingest call
    needed to give ``GET /chunks/{id}/work-items`` a pointer to render."""
    hub = build_hub(tmp_path)
    created = hub.client.post(
        "/api/work-sources/hub/items", json={"title": "widget is broken", "body": "steps to repro"}
    ).json()
    chunk_id = created["chunk_id"]

    entries = hub.client.get(f"/api/chunks/{chunk_id}/work-items").json()["items"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["source"] == "hub"
    assert entry["ref"] == created["ref"]
    assert entry["label"] == f"hub:{created['ref']}"
    assert entry["title"] == "widget is broken"
    assert entry["body"] == "steps to repro"
    assert entry["web_url"] == f"/board/chunk/{chunk_id}"
    assert entry["error"] is None


# --------------------------------------------------------------------------- #
# GET /api/work-sources — the capability-boolean listing


def test_sources_listing_renders_capability_booleans(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"forge": FakeWorkSource(name="forge")})

    sources = {row["name"]: row for row in hub.client.get("/api/work-sources").json()["sources"]}

    assert sources["hub"]["edit"] is True
    assert sources["forge"]["edit"] is False
    assert sources["forge"]["annotate"] is False
    assert sources["forge"]["close"] is False


# --------------------------------------------------------------------------- #
# The six routes' stated shapes


def test_create_get_list_patch_and_withdraw_round_trip(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    created = hub.client.post(
        "/api/work-sources/hub/items", json={"title": "widget is broken", "body": "steps to repro"}
    )
    assert created.status_code == 201, created.text
    body = created.json()
    ref = body["ref"]
    assert body["label"] == f"hub:{ref}"
    assert body["title"] == "widget is broken"
    assert body["stated_priority"] == "normal"
    assert body["closure"] is None
    chunk_id = body["chunk_id"]
    assert body["web_url"] == f"/board/chunk/{chunk_id}"  # live from the moment create mints the holder
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "not_ready"

    listed = hub.client.get("/api/work-sources/hub/items").json()["items"]
    assert [item["ref"] for item in listed] == [ref]

    fetched = hub.client.get(f"/api/work-sources/hub/items/{ref}")
    assert fetched.status_code == 200
    assert fetched.json()["title"] == "widget is broken"
    assert fetched.json()["web_url"] == f"/board/chunk/{chunk_id}"

    patched = hub.client.patch(f"/api/work-sources/hub/items/{ref}", json={"title": "widget is fixed"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["title"] == "widget is fixed"
    assert patched.json()["body"] == "steps to repro"  # untouched field is preserved

    # The minted chunk is still not_ready — unacquired, not genuinely live — so
    # withdrawal deletes it rather than refusing (issue #364, D3).
    withdrawn = hub.client.delete(f"/api/work-sources/hub/items/{ref}")
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["closure"] == "withdrawn"
    assert withdrawn.json()["closed_at"] is not None
    assert withdrawn.json()["web_url"] is None  # its holding chunk is gone
    assert hub.client.get(f"/api/chunks/{chunk_id}").status_code == 404


# --------------------------------------------------------------------------- #
# blizzard#359 — create mints its resting chunk, one transaction, no promotion


def test_create_mints_exactly_one_chunk_on_the_default_graph_holding_the_new_pointer(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    default = hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )

    created = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()

    chunks = hub.client.get("/api/chunks").json()
    assert [chunk["chunk_id"] for chunk in chunks] == [created["chunk_id"]]
    assert chunks[0]["graph_id"] == default.graph_id
    assert [(ref["source"], ref["ref"]) for ref in chunks[0]["work_refs"]] == [("hub", created["ref"])]


def test_create_publishes_a_minted_chunk_changed_frame(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    created = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()

    frames = [json.loads(e["data"]) for e in emitted_events(hub) if e["event"] == CHUNK_CHANGED]
    assert len(frames) == 1
    assert frames[0]["chunk_id"] == created["chunk_id"]
    assert frames[0]["cause"] == "minted"
    assert frames[0]["status"] == "not_ready"


def test_create_also_publishes_queue_changed_since_the_mint_joins_the_backlog_list(tmp_path: Path) -> None:
    """Mirrors ``POST /chunks``: minting a chunk changes ``not_ready``-list membership,
    so the board's backlog query must invalidate the same way an out-of-band ingest
    does — a gap the editor path missed when ``POST /chunks`` was fixed for it."""
    hub = build_hub(tmp_path)

    hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"})

    assert [e["event"] for e in emitted_events(hub)] == [CHUNK_CHANGED, QUEUE_CHANGED]


def test_a_second_post_chunks_against_the_minted_pointer_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    created = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()

    conflict = hub.client.post("/api/chunks", json={"tokens": [f"hub:{created['ref']}"]})

    assert conflict.status_code == 409
    assert conflict.json()["existing_chunk_id"] == created["chunk_id"]


def test_create_is_409_when_an_out_of_band_ingest_already_holds_the_allocated_ref(tmp_path: Path) -> None:
    """``HubWorkSource.parse``/``fetch`` admit any digit-string ref, allocated or not, so an
    out-of-band ``POST /chunks`` can pre-empt the very ref allocation mints next — without
    the guard, create mints a second live chunk holding the pointer the ingest already holds."""
    hub = build_hub(tmp_path)
    pre_ingested = hub.client.post("/api/chunks", json={"tokens": ["hub:1"]})
    assert pre_ingested.status_code == 201, pre_ingested.text
    existing_chunk_id = pre_ingested.json()["chunk_id"]

    conflict = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"})

    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["existing_chunk_id"] == existing_chunk_id
    assert hub.client.get("/api/work-sources/hub/items").json()["items"] == []


def test_create_does_not_promote_and_priority_never_writes_a_queue_position(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    created = hub.client.post(
        "/api/work-sources/hub/items", json={"title": "t", "body": "b", "stated_priority": "high"}
    ).json()

    assert created["chunk_id"] not in hub.services.chunks.queue_positions()
    assert hub.client.get("/api/queue").json()["entries"] == []

    promote = hub.client.post(f"/api/chunks/{created['chunk_id']}/promote")
    assert promote.status_code == 202
    positions = hub.services.chunks.queue_positions()
    assert created["chunk_id"] in positions  # the tail stamp promotion always writes
    assert [e["chunk_id"] for e in hub.client.get("/api/queue").json()["entries"]] == [created["chunk_id"]]


def test_create_against_a_retired_default_graph_is_503_and_writes_no_item(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    doc = hub.services.default_graph_doc
    graph = hub.services.graph_mint.ensure_default(doc, definition_yaml=hub.services.default_graph_yaml)
    hub.services.graph_lifecycle.retire(graph, by="operator")

    resp = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"})

    assert resp.status_code == 503, resp.text
    assert doc.name in resp.json()["detail"]
    assert hub.client.get("/api/work-sources/hub/items").json()["items"] == []


def test_a_blank_title_rejects_before_the_default_graph_is_resolved(tmp_path: Path) -> None:
    """Resolving the graph is a durable write, so a rejected request must not perform it:
    the blank title is 422 with no graph minted, and stays 422 rather than becoming the
    retired graph's 503 once one has been minted and retired."""
    hub = build_hub(tmp_path)

    blank = hub.client.post("/api/work-sources/hub/items", json={"title": "  ", "body": "b"})

    assert blank.status_code == 422, blank.text
    assert hub.client.get("/api/graphs").json() == []

    graph = hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )
    hub.services.graph_lifecycle.retire(graph, by="operator")

    retired = hub.client.post("/api/work-sources/hub/items", json={"title": "  ", "body": "b"})

    assert retired.status_code == 422, retired.text


# --------------------------------------------------------------------------- #
# D4 — the editor-presence gate: 404 unknown source, 409 no editor


def test_unknown_source_is_404_on_every_source_addressed_route(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    assert hub.client.get("/api/work-sources/nope/items").status_code == 404
    assert hub.client.post("/api/work-sources/nope/items", json={"title": "t", "body": "b"}).status_code == 404
    assert hub.client.get("/api/work-sources/nope/items/1").status_code == 404
    assert hub.client.patch("/api/work-sources/nope/items/1", json={"title": "t"}).status_code == 404
    assert hub.client.delete("/api/work-sources/nope/items/1").status_code == 404


def test_a_source_with_no_editor_is_409_on_every_source_addressed_route(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"forge": FakeWorkSource(name="forge")})

    assert hub.client.get("/api/work-sources/forge/items").status_code == 409
    assert hub.client.post("/api/work-sources/forge/items", json={"title": "t", "body": "b"}).status_code == 409
    assert hub.client.get("/api/work-sources/forge/items/1").status_code == 409
    assert hub.client.patch("/api/work-sources/forge/items/1", json={"title": "t"}).status_code == 409
    assert hub.client.delete("/api/work-sources/forge/items/1").status_code == 409


# --------------------------------------------------------------------------- #
# D9 — an unallocated ref is 404


def test_an_unallocated_ref_is_404_on_get_patch_and_delete(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    assert hub.client.get("/api/work-sources/hub/items/999").status_code == 404
    assert hub.client.patch("/api/work-sources/hub/items/999", json={"title": "t"}).status_code == 404
    assert hub.client.delete("/api/work-sources/hub/items/999").status_code == 404


# --------------------------------------------------------------------------- #
# D5 — a closed item refuses PATCH and DELETE alike


def test_patch_and_delete_of_an_already_withdrawn_item_are_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    body = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()
    ref = body["ref"]
    hub.client.post(f"/api/chunks/{body['chunk_id']}/stop", json={})
    hub.client.delete(f"/api/work-sources/hub/items/{ref}")

    assert hub.client.patch(f"/api/work-sources/hub/items/{ref}", json={"title": "t2"}).status_code == 409
    assert hub.client.delete(f"/api/work-sources/hub/items/{ref}").status_code == 409


def test_the_listing_route_threads_its_limit_and_refuses_one_out_of_range(tmp_path: Path) -> None:
    """The bound is the route's, not just the store's: a `limit` the handler declares but
    never passes on would leave `GET .../items` the unbounded full-table scan it was, with
    the store-tier limit test still green."""
    hub = build_hub(tmp_path)
    for i in range(3):
        hub.client.post("/api/work-sources/hub/items", json={"title": f"t{i}", "body": "b"})

    assert len(hub.client.get("/api/work-sources/hub/items", params={"limit": 2}).json()["items"]) == 2
    assert len(hub.client.get("/api/work-sources/hub/items").json()["items"]) == 3  # under the default cap
    assert hub.client.get("/api/work-sources/hub/items", params={"limit": 0}).status_code == 422
    assert hub.client.get("/api/work-sources/hub/items", params={"limit": 1001}).status_code == 422


def test_delete_deletes_an_unacquired_holder_and_returns_200(tmp_path: Path) -> None:
    """D3 (issue #364): the freshly-minted holder is not_ready — unacquired, not
    genuinely live — so DELETE succeeds immediately, publishing the same
    chunk-changed/queue-changed pair a direct chunk delete does (blizzard#359)."""
    hub = build_hub(tmp_path)
    created = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()
    ref, chunk_id = created["ref"], created["chunk_id"]
    since = hub.events.latest_id()

    assert hub.client.delete(f"/api/work-sources/hub/items/{ref}").status_code == 200
    assert hub.client.get(f"/api/chunks/{chunk_id}").status_code == 404

    events = emitted_events(hub, since=since)
    types = [e["event"] for e in events]
    assert CHUNK_CHANGED in types
    assert QUEUE_CHANGED in types
    frames = [json.loads(e["data"]) for e in events if e["event"] == CHUNK_CHANGED]
    assert len(frames) == 1
    frame = frames[0]
    assert frame["chunk_id"] == chunk_id
    assert frame["cause"] == "deleted"
    assert frame["status"] == "not_ready"
    assert frame["by"] == "operator"  # AUTH_MODE_NONE's implicit identity (issue #364)
    assert frame["key"].startswith("chunk_deleted:")


def test_delete_is_409_while_an_acquired_chunk_holds_the_item_and_200_once_it_is_stopped(tmp_path: Path) -> None:
    """A claimed (running) holder is genuinely acquired — outside
    ``GROUPABLE_STATUSES`` — so DELETE still refuses it, exactly as before (D3). A
    terminal holder's own withdrawal is unaffected either way, deleting nothing."""
    hub = build_hub(tmp_path)
    created = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()
    ref, chunk_id = created["ref"], created["chunk_id"]
    claimed = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claimed.status_code == 201, claimed.text

    assert hub.client.delete(f"/api/work-sources/hub/items/{ref}").status_code == 409

    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={}).status_code == 202

    assert hub.client.delete(f"/api/work-sources/hub/items/{ref}").status_code == 200
    assert hub.client.get(f"/api/chunks/{chunk_id}").status_code == 200  # a terminal holder survives (D3)


# --------------------------------------------------------------------------- #
# D6 — authorship is stamped from the session, never accepted from the body


def test_create_stamps_the_caller_s_user_id_not_their_username(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    user = seed_user(hub, username="alice", role=Role.CONTRIBUTOR)
    token = seed_session(hub, user)
    assert user.user_id != user.username

    created = hub.client.post(
        "/api/work-sources/hub/items",
        json={"title": "t", "body": "b"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert created.status_code == 201, created.text
    assert created.json()["author"] == {
        "kind": "user",
        "user_id": user.user_id,
        "login": "alice",
        "runner_id": None,
        "chunk_id": None,
        "node_name": None,
    }


def test_create_carrying_a_client_supplied_author_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b", "author": "someone-else"})

    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# D7 — stated_priority is a validated enum, defaulting to normal


def test_stated_priority_outside_the_three_values_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b", "stated_priority": "urgent"})

    assert resp.status_code == 422


def test_stated_priority_omitted_persists_normal(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    created = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"})

    assert created.json()["stated_priority"] == "normal"


def test_patch_omitting_stated_priority_leaves_it_unchanged(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ref = hub.client.post(
        "/api/work-sources/hub/items", json={"title": "t", "body": "b", "stated_priority": "high"}
    ).json()["ref"]

    patched = hub.client.patch(f"/api/work-sources/hub/items/{ref}", json={"title": "t2"})

    assert patched.json()["stated_priority"] == "high"


def test_patch_carrying_an_explicit_null_stated_priority_clears_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ref = hub.client.post(
        "/api/work-sources/hub/items", json={"title": "t", "body": "b", "stated_priority": "high"}
    ).json()["ref"]

    patched = hub.client.patch(f"/api/work-sources/hub/items/{ref}", json={"stated_priority": None})

    assert patched.status_code == 200, patched.text
    assert patched.json()["stated_priority"] is None


# --------------------------------------------------------------------------- #
# A blank title or body is 422 — the same shape ``api/chunks.py``'s edit refuses


def test_create_with_a_blank_title_or_body_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    assert hub.client.post("/api/work-sources/hub/items", json={"title": "  ", "body": "b"}).status_code == 422
    assert hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "  "}).status_code == 422


def test_patch_with_a_blank_title_or_body_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ref = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()["ref"]

    assert hub.client.patch(f"/api/work-sources/hub/items/{ref}", json={"title": "  "}).status_code == 422
    assert hub.client.patch(f"/api/work-sources/hub/items/{ref}", json={"body": "  "}).status_code == 422


# --------------------------------------------------------------------------- #
# A runner bearer token is refused on all six routes (``reject_runner_principal``)


def test_runner_bearer_token_is_rejected_on_every_work_source_route(tmp_path: Path) -> None:
    from tests.test_fleet_auth import _bearer, _seed_enrolled

    token = _seed_enrolled(tmp_path)
    warn_hub = build_hub(tmp_path)
    ref = warn_hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()["ref"]

    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)
    headers = _bearer(token)
    assert hub.client.get("/api/work-sources", headers=headers).status_code == 403
    assert hub.client.get("/api/work-sources/hub/items", headers=headers).status_code == 403
    assert (
        hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}, headers=headers).status_code
        == 403
    )
    assert hub.client.get(f"/api/work-sources/hub/items/{ref}", headers=headers).status_code == 403
    assert (
        hub.client.patch(f"/api/work-sources/hub/items/{ref}", json={"title": "t2"}, headers=headers).status_code == 403
    )
    assert hub.client.delete(f"/api/work-sources/hub/items/{ref}", headers=headers).status_code == 403
