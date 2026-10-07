"""Routines as configured records over HTTP (component tier): the sparse ``PATCH``, an
older CLI's full payload still succeeding, ``If-Match`` conflicts, the door and the
authenticated actor on each change row, and retire/enable/link writes that change nothing
writing nothing — a real hub app driven through ``TestClient``."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.auth_core import Role
from tests.support import HubHarness, build_hub, seed_session, seed_user

pytestmark = pytest.mark.component

_GRAPH = """
name: {name}
entry: build
nodes:
  build:
    executor: runner
    prompt: do the work
    judgement:
      prompt: judge it
      choices:
        pass:
          description: it works
          to: done
"""


def _mint_graph(hub: HubHarness, name: str = "alpha", headers: dict[str, str] | None = None) -> None:
    resp = hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH.format(name=name)}, headers=headers)
    assert resp.status_code == 201, resp.text


def _create(hub: HubHarness, headers: dict[str, str] | None = None, **overrides: object) -> dict:  # type: ignore[type-arg]
    body = {"name": "nightly", "graph_name": "alpha", "default_scope_slug": "blizzard", **overrides}
    resp = hub.client.post("/api/routines", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _changes(hub: HubHarness, kind: str = "routine", headers: dict[str, str] | None = None) -> list[dict]:  # type: ignore[type-arg]
    resp = hub.client.get("/api/config/changes", params={"record_kind": kind}, headers=headers)
    assert resp.status_code == 200, resp.text
    return list(reversed(resp.json()["changes"]))


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    return hub


def test_create_is_revision_one_and_logs_the_routine_and_its_minted_scope(hub: HubHarness) -> None:
    body = _create(hub)

    assert body["revision"] == 1
    [row] = _changes(hub)
    assert (row["record_key"], row["revision"], row["op"]) == ("nightly", 1, "create")
    assert [(r["record_key"], r["op"]) for r in _changes(hub, "scope")] == [("blizzard", "create")]


def test_a_sparse_patch_sets_only_its_field_and_moves_the_revision_by_one(hub: HubHarness) -> None:
    created = _create(hub, default_model=["blizzard:advanced"])

    resp = hub.client.patch(f"/api/routines/{created['routine_id']}", json={"default_effort": "high"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["default_effort"], body["default_model"], body["revision"]) == ("high", ["blizzard:advanced"], 2)
    row = _changes(hub)[-1]
    assert (row["op"], row["revision"]) == ("edit", 2)
    assert row["diff"] == [{"field": "default_effort", "old": None, "new": "high"}]


def test_a_patch_restating_a_since_retired_graph_applies_the_other_fields(hub: HubHarness) -> None:
    rid = _create(hub)["routine_id"]
    graph_id = hub.client.get("/api/graphs").json()[0]["graph_id"]
    assert hub.client.post(f"/api/graphs/{graph_id}/retire", json={}).status_code in (200, 202)

    resp = hub.client.patch(f"/api/routines/{rid}", json={"graph_name": "alpha", "default_effort": "high"})

    assert resp.status_code == 200, resp.text
    assert (resp.json()["default_effort"], resp.json()["revision"]) == ("high", 2)


def test_an_older_clis_full_payload_still_succeeds(hub: HubHarness) -> None:
    _mint_graph(hub, "beta")
    created = _create(hub)

    resp = hub.client.patch(
        f"/api/routines/{created['routine_id']}",
        json={
            "name": "nightly",
            "graph_name": "beta",
            "default_scope_slug": "other",
            "default_model": ["blizzard:advanced"],
            "default_effort": "high",
            "default_harnesses": ["claude_code"],
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["graph_name"], body["default_scope_slug"], body["revision"]) == ("beta", "other", 2)
    assert hub.client.get(f"/api/routines/{created['routine_id']}/scopes").json() == ["blizzard", "other"]


def test_restating_the_whole_record_writes_nothing(hub: HubHarness) -> None:
    created = _create(hub)
    restated = {k: created[k] for k in ("name", "graph_name", "default_scope_slug", "default_model")}

    resp = hub.client.patch(f"/api/routines/{created['routine_id']}", json=restated)

    assert resp.json()["revision"] == 1
    assert len(_changes(hub)) == 1


def test_null_clears_effort_and_is_refused_elsewhere_naming_the_field(hub: HubHarness) -> None:
    created = _create(hub, default_effort="high")
    path = f"/api/routines/{created['routine_id']}"

    assert hub.client.patch(path, json={"default_effort": None}).json()["default_effort"] is None
    refused = hub.client.patch(path, json={"graph_name": None})
    assert refused.status_code == 422
    assert refused.json()["detail"][0]["loc"] == ["body", "graph_name"]


def test_a_name_change_and_an_unknown_field_are_refused(hub: HubHarness) -> None:
    created = _create(hub)
    path = f"/api/routines/{created['routine_id']}"

    assert hub.client.patch(path, json={"name": "renamed"}).status_code == 422
    assert hub.client.patch(path, json={"routine_id": "rtn_x"}).status_code == 422


def test_a_stale_if_match_is_409_and_writes_nothing(hub: HubHarness) -> None:
    created = _create(hub)
    rid = created["routine_id"]

    for resp in (
        hub.client.patch(f"/api/routines/{rid}", json={"default_effort": "high"}, headers={"If-Match": "7"}),
        hub.client.post(f"/api/routines/{rid}/retire", json={}, headers={"If-Match": "7"}),
        hub.client.put(f"/api/routines/{rid}/scopes/blizzard", headers={"If-Match": "7"}),
    ):
        assert resp.status_code == 409, resp.text
        assert "revision 1" in resp.json()["detail"]
    assert len(_changes(hub)) == 1
    current = hub.client.patch(f"/api/routines/{rid}", json={"default_effort": "high"}, headers={"If-Match": "1"})
    assert current.status_code == 200


def test_a_repeated_retire_or_enable_writes_nothing(hub: HubHarness) -> None:
    created = _create(hub)
    rid = created["routine_id"]

    first = hub.client.post(f"/api/routines/{rid}/retire", json={})
    second = hub.client.post(f"/api/routines/{rid}/retire", json={})
    hub.client.post(f"/api/routines/{rid}/enable", json={})
    again = hub.client.post(f"/api/routines/{rid}/enable", json={})

    assert (first.status_code, first.json()["retired"], first.json()["revision"]) == (202, True, 2)
    assert (second.json()["retired"], second.json()["revision"]) == (True, 2)
    assert (again.json()["retired"], again.json()["revision"]) == (False, 3)
    assert [r["op"] for r in _changes(hub)] == ["create", "retire", "enable"]
    with hub.engine.connect() as conn:
        facts = conn.exec_driver_sql("SELECT retired, set_by FROM routine_lifecycle_facts ORDER BY id").all()
    assert [tuple(f) for f in facts] == [(True, "operator"), (False, "operator")]


def test_link_and_unlink_are_edits_of_scopes_and_a_repeat_writes_nothing(hub: HubHarness) -> None:
    created = _create(hub)
    rid = created["routine_id"]
    assert hub.client.post("/api/scopes", json={"slug": "other"}).status_code == 201

    assert hub.client.put(f"/api/routines/{rid}/scopes/other").status_code == 204
    assert hub.client.put(f"/api/routines/{rid}/scopes/other").status_code == 204
    assert hub.client.delete(f"/api/routines/{rid}/scopes/other").status_code == 204
    assert hub.client.delete(f"/api/routines/{rid}/scopes/other").status_code == 204

    rows = _changes(hub)[1:]
    assert [(r["op"], r["revision"]) for r in rows] == [("edit", 2), ("edit", 3)]
    assert rows[0]["diff"] == [{"field": "scopes", "old": ["blizzard"], "new": ["blizzard", "other"]}]
    assert hub.client.get(f"/api/routines/{rid}").json()["revision"] == 3


def test_the_change_row_carries_the_door_and_the_authenticated_actor_not_by(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    user = seed_user(hub, username="ada", role=Role.ADMIN)
    admin = {"Authorization": f"Bearer {seed_session(hub, user)}"}
    _mint_graph(hub, headers=admin)
    created = _create(hub, headers={**admin, "X-Blizzard-Door": "board"})

    retired = hub.client.post(
        f"/api/routines/{created['routine_id']}/retire",
        json={"by": "someone-else"},
        headers={**admin, "X-Blizzard-Door": "cli"},
    )

    assert retired.status_code == 202, retired.text
    rows = _changes(hub, headers=admin)
    assert [(r["door"], r["actor"]) for r in rows] == [("board", user.user_id), ("cli", user.user_id)]
    with hub.engine.connect() as conn:
        (set_by,) = conn.exec_driver_sql("SELECT set_by FROM routine_lifecycle_facts").one()
    assert set_by == "someone-else"
