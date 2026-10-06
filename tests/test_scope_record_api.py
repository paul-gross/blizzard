"""Scopes as configured records over HTTP (component tier): the sparse ``PATCH``, an
older CLI's ``{description}`` payload, ``If-Match`` conflicts, the door and the
authenticated actor on each change row, and retire/enable writes that change nothing
writing nothing — a real hub app driven through ``TestClient``."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.auth_core import Role
from tests.support import HubHarness, build_hub, seed_session, seed_user

pytestmark = pytest.mark.component


def _changes(hub: HubHarness, headers: dict[str, str] | None = None) -> list[dict]:  # type: ignore[type-arg]
    resp = hub.client.get("/api/config/changes", params={"record_kind": "scope"}, headers=headers)
    assert resp.status_code == 200, resp.text
    return list(reversed(resp.json()["changes"]))


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/scopes", json={"slug": "blizzard", "description": "old"}).status_code == 201
    return hub


def test_create_is_revision_one_and_a_repeat_create_writes_nothing(hub: HubHarness) -> None:
    again = hub.client.post("/api/scopes", json={"slug": "blizzard", "description": "other"})

    assert (again.json()["revision"], again.json()["description"]) == (1, "old")
    [row] = _changes(hub)
    assert (row["record_key"], row["revision"], row["op"]) == ("blizzard", 1, "create")
    assert row["diff"] == [{"field": "description", "old": None, "new": "old"}]


def test_an_older_clis_description_edit_moves_the_revision_and_logs_it(hub: HubHarness) -> None:
    resp = hub.client.patch("/api/scopes/blizzard", json={"description": "new"})

    assert resp.status_code == 200, resp.text
    assert (resp.json()["description"], resp.json()["revision"]) == ("new", 2)
    row = _changes(hub)[-1]
    assert (row["op"], row["diff"]) == ("edit", [{"field": "description", "old": "old", "new": "new"}])


def test_an_edit_that_changes_nothing_writes_nothing(hub: HubHarness) -> None:
    for body in ({}, {"description": "old"}):
        assert hub.client.patch("/api/scopes/blizzard", json=body).json()["revision"] == 1
    assert len(_changes(hub)) == 1


def test_null_and_an_unknown_field_are_refused(hub: HubHarness) -> None:
    refused = hub.client.patch("/api/scopes/blizzard", json={"description": None})

    assert refused.status_code == 422
    assert refused.json()["detail"][0]["loc"] == ["body", "description"]
    assert hub.client.patch("/api/scopes/blizzard", json={"slug": "x"}).status_code == 422


def test_a_stale_if_match_is_409_and_writes_nothing(hub: HubHarness) -> None:
    for resp in (
        hub.client.patch("/api/scopes/blizzard", json={"description": "new"}, headers={"If-Match": "4"}),
        hub.client.post("/api/scopes/blizzard/retire", json={}, headers={"If-Match": "4"}),
        hub.client.post("/api/scopes/blizzard/enable", json={}, headers={"If-Match": "4"}),
    ):
        assert resp.status_code == 409, resp.text
    assert len(_changes(hub)) == 1


def test_a_repeated_retire_or_enable_writes_nothing(hub: HubHarness) -> None:
    first = hub.client.post("/api/scopes/blizzard/retire", json={})
    second = hub.client.post("/api/scopes/blizzard/retire", json={})
    enabled = hub.client.post("/api/scopes/blizzard/enable", json={})
    again = hub.client.post("/api/scopes/blizzard/enable", json={})

    assert (first.status_code, first.json()["retired"], first.json()["revision"]) == (202, True, 2)
    assert second.json()["revision"] == 2
    assert (enabled.json()["retired"], enabled.json()["revision"]) == (False, 3)
    assert again.json()["revision"] == 3
    assert [r["op"] for r in _changes(hub)] == ["create", "retire", "enable"]
    with hub.engine.connect() as conn:
        (facts,) = conn.exec_driver_sql("SELECT count(*) FROM scope_lifecycle_facts").one()
    assert facts == 2


def test_the_change_row_carries_the_door_and_the_authenticated_actor(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    user = seed_user(hub, username="ada", role=Role.ADMIN)
    admin = {"Authorization": f"Bearer {seed_session(hub, user)}"}

    created = hub.client.post("/api/scopes", json={"slug": "blizzard"}, headers={**admin, "X-Blizzard-Door": "cli"})
    edited = hub.client.patch(
        "/api/scopes/blizzard", json={"description": "d"}, headers={**admin, "X-Blizzard-Door": "board"}
    )

    assert (created.status_code, edited.status_code) == (201, 200)
    rows = _changes(hub, headers=admin)
    assert [(r["door"], r["actor"]) for r in rows] == [("cli", user.user_id), ("board", user.user_id)]
