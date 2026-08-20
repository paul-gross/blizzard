"""``GET /chunks/{id}/work-items`` renders a hub-owned pointer through the unchanged
handler (issue #357, component tier) — the built-in ``hub`` source needs no
``[[work_source]]`` to resolve at ingest or render at read.

Also exercises the source-addressed editor routes (blizzard#358): ``/api/work-sources``
and its ``{source}/items``/``{source}/items/{ref}`` children."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.auth_core import Role
from blizzard.hub.config import RUNNER_AUTH_ENFORCE
from blizzard.hub.domain.work import WorkItemAuthor
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support import FakeWorkSource, build_hub, seed_session, seed_user

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_a_hub_owned_pointer_ingests_and_renders_its_title_and_body(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    item = WorkItemStore(hub.engine).create(
        source="hub",
        title="widget is broken",
        body="steps to repro",
        author=WorkItemAuthor.fleet(),
        stated_priority=None,
        at=_T0,
    )

    chunk_id = hub.client.post("/api/chunks", json={"tokens": [f"hub:{item.ref}"]}).json()["chunk_id"]

    entries = hub.client.get(f"/api/chunks/{chunk_id}/work-items").json()["items"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["source"] == "hub"
    assert entry["ref"] == item.ref
    assert entry["label"] == f"hub:{item.ref}"
    assert entry["title"] == "widget is broken"
    assert entry["body"] == "steps to repro"
    assert entry["web_url"] == f"/board/chunk/{chunk_id}"
    assert entry["error"] is None


# --------------------------------------------------------------------------- #
# GET /api/work-sources — the capability-boolean listing


def test_sources_listing_renders_capability_booleans(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, work_sources={"forge": FakeWorkSource(name="forge")})

    sources = {row["name"]: row for row in hub.client.get("/api/work-sources").json()}

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

    listed = hub.client.get("/api/work-sources/hub/items").json()["items"]
    assert [item["ref"] for item in listed] == [ref]

    fetched = hub.client.get(f"/api/work-sources/hub/items/{ref}")
    assert fetched.status_code == 200
    assert fetched.json()["title"] == "widget is broken"

    patched = hub.client.patch(f"/api/work-sources/hub/items/{ref}", json={"title": "widget is fixed"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["title"] == "widget is fixed"
    assert patched.json()["body"] == "steps to repro"  # untouched field is preserved

    withdrawn = hub.client.delete(f"/api/work-sources/hub/items/{ref}")
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["closure"] == "withdrawn"
    assert withdrawn.json()["closed_at"] is not None


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
    ref = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()["ref"]
    hub.client.delete(f"/api/work-sources/hub/items/{ref}")

    assert hub.client.patch(f"/api/work-sources/hub/items/{ref}", json={"title": "t2"}).status_code == 409
    assert hub.client.delete(f"/api/work-sources/hub/items/{ref}").status_code == 409


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
    assert created.json()["author"] == {"kind": "user", "user_id": user.user_id}


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
