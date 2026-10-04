"""The work-source record verbs, the served schema, and the change-log read (component tier) —
a real hub app driven through ``TestClient``."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.auth_core import Role
from tests.support import HubHarness, build_hub, seed_session, seed_user

pytestmark = pytest.mark.component

_SENTINEL = "tok-planted-840"
_DEMO = {"name": "demo", "provider": "github", "locator": "acme/demo", "secret": "gh", "annotate": True}


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/secrets", json={"name": "gh", "value": _SENTINEL}).status_code == 201
    return hub


def _create(hub: HubHarness, **overrides: object):  # type: ignore[no-untyped-def]
    return hub.client.post("/api/work-sources", json={**_DEMO, **overrides})


def _changes(hub: HubHarness, **params: object) -> list[dict]:
    resp = hub.client.get("/api/config/changes", params=params)  # type: ignore[arg-type]
    assert resp.status_code == 200, resp.text
    return resp.json()["changes"]


def test_create_show_list_and_the_built_in_hub(hub: HubHarness) -> None:
    created = _create(hub)
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["name"], body["revision"], body["retired"], body["built_in"]) == ("demo", 1, False, False)
    assert (body["provider"], body["locator"], body["secret"], body["annotate"]) == (
        "github",
        "acme/demo",
        "gh",
        True,
    )
    assert body["created_by"] == "operator"
    assert hub.client.get("/api/work-sources/demo").json() == body

    listed = hub.client.get("/api/work-sources").json()["sources"]
    by_name = {s["name"]: s for s in listed}
    assert by_name["hub"]["built_in"] is True
    assert by_name["hub"]["provider"] is None
    assert by_name["hub"]["edit"] is True
    assert by_name["demo"]["edit"] is False
    assert by_name["demo"]["annotate"] is True
    assert {"name", "annotate", "edit"} <= set(by_name["demo"])
    assert hub.client.get("/api/work-sources/hub").json()["built_in"] is True
    assert hub.client.get("/api/work-sources/missing").status_code == 404


def test_the_create_422s_name_the_offending_field(hub: HubHarness) -> None:
    for overrides, field in (
        ({"name": "a:b"}, "name"),
        ({"name": "hub"}, "name"),
        ({"provider": "svn"}, "provider"),
        ({"locator": "nope"}, "locator"),
        ({"secret": "absent"}, "secret"),
        ({"secret": None}, "secret"),
    ):
        resp = _create(hub, **overrides)
        assert resp.status_code == 422, (overrides, resp.text)
        assert resp.json()["detail"][0]["loc"][-1] == field, overrides
    assert hub.client.post("/api/work-sources", json={**_DEMO, "bogus": 1}).status_code == 422
    assert _changes(hub, record_kind="work_source") == []


def test_a_retired_secret_is_a_422_on_create_and_on_enable(hub: HubHarness) -> None:
    assert _create(hub).status_code == 201
    hub.client.post("/api/work-sources/demo/retire")
    assert hub.client.post("/api/secrets/gh/retire").status_code == 200
    assert _create(hub, name="other", locator="acme/other").status_code == 422
    enabled = hub.client.post("/api/work-sources/demo/enable")
    assert enabled.status_code == 422
    assert enabled.json()["detail"][0]["loc"][-1] == "secret"
    assert hub.client.patch("/api/work-sources/demo", json={"annotate": False}).status_code == 422


def test_a_taken_name_or_locator_is_a_409_naming_the_holder(hub: HubHarness) -> None:
    assert _create(hub).status_code == 201
    assert _create(hub).status_code == 409
    clash = _create(hub, name="demo2")
    assert clash.status_code == 409
    assert "demo" in clash.json()["detail"]
    hub.client.post("/api/work-sources/demo/retire")
    held = _create(hub, name="demo2")
    assert held.status_code == 409
    assert "work source demo" in held.json()["detail"]


def test_patch_is_sparse_and_tells_absent_from_null(hub: HubHarness) -> None:
    _create(hub, api_base="https://ghe.example/api/v3", web_base="https://ghe.example")

    untouched = hub.client.patch("/api/work-sources/demo", json={})
    assert untouched.status_code == 200
    assert untouched.json()["revision"] == 1
    same = hub.client.patch("/api/work-sources/demo", json={"annotate": True, "secret": "gh"})
    assert same.json()["revision"] == 1

    edited = hub.client.patch("/api/work-sources/demo", json={"annotate": False}).json()
    assert (edited["revision"], edited["annotate"], edited["api_base"]) == (2, False, "https://ghe.example/api/v3")
    cleared = hub.client.patch("/api/work-sources/demo", json={"web_base": None}).json()
    assert (cleared["revision"], cleared["web_base"], cleared["api_base"]) == (3, None, "https://ghe.example/api/v3")

    for body, field in (
        ({"secret": None}, "secret"),
        ({"annotate": None}, "annotate"),
        ({"provider": None}, "provider"),
    ):
        resp = hub.client.patch("/api/work-sources/demo", json=body)
        assert resp.status_code == 422, body
        assert resp.json()["detail"][0]["loc"][-1] == field
    assert hub.client.patch("/api/work-sources/demo", json={"name": "other"}).status_code == 422
    assert hub.client.patch("/api/work-sources/nope", json={}).status_code == 404
    assert hub.client.get("/api/work-sources/demo").json()["revision"] == 3


def test_if_match_is_checked_on_patch_retire_and_enable(hub: HubHarness) -> None:
    _create(hub)
    hub.client.patch("/api/work-sources/demo", json={"annotate": False})
    for call in (
        lambda h: hub.client.patch("/api/work-sources/demo", json={"annotate": True}, headers=h),
        lambda h: hub.client.post("/api/work-sources/demo/retire", headers=h),
        lambda h: hub.client.post("/api/work-sources/demo/enable", headers=h),
    ):
        stale = call({"If-Match": "1"})
        assert stale.status_code == 409
        assert "revision 2" in stale.json()["detail"]
    assert (
        hub.client.patch("/api/work-sources/demo", json={"annotate": True}, headers={"If-Match": "2"}).status_code
        == 200
    )


def test_retire_and_enable_move_the_revision_and_are_idempotent(hub: HubHarness) -> None:
    _create(hub)
    retired = hub.client.post("/api/work-sources/demo/retire").json()
    assert (retired["revision"], retired["retired"]) == (2, True)
    assert hub.client.post("/api/work-sources/demo/retire").json()["revision"] == 2
    assert "demo" not in {s["name"] for s in hub.client.get("/api/work-sources").json()["sources"]}
    listed = hub.client.get("/api/work-sources", params={"include_retired": True}).json()["sources"]
    assert "demo" in {s["name"] for s in listed}
    enabled = hub.client.post("/api/work-sources/demo/enable").json()
    assert (enabled["revision"], enabled["retired"]) == (3, False)
    assert hub.client.post("/api/work-sources/demo/enable").json()["revision"] == 3
    assert [c["op"] for c in _changes(hub, record_kind="work_source")] == ["enable", "retire", "create"]


def test_the_built_in_hub_is_never_written(hub: HubHarness) -> None:
    for resp in (
        hub.client.patch("/api/work-sources/hub", json={"annotate": True}),
        hub.client.post("/api/work-sources/hub/retire"),
        hub.client.post("/api/work-sources/hub/enable"),
    ):
        assert resp.status_code == 409


def test_a_referenced_secret_cannot_be_retired_and_shows_its_references(hub: HubHarness) -> None:
    _create(hub)
    shown = hub.client.get("/api/secrets/gh").json()
    assert shown["references"] == [{"kind": "work_source", "key": "demo"}]
    assert hub.client.get("/api/secrets").json()[0]["references"] == shown["references"]
    refused = hub.client.post("/api/secrets/gh/retire")
    assert refused.status_code == 409
    assert "work_source demo" in refused.json()["detail"]
    assert hub.client.get("/api/secrets/gh").json()["retired"] is False
    hub.client.post("/api/work-sources/demo/retire")
    assert hub.client.get("/api/secrets/gh").json()["references"] == []
    assert hub.client.post("/api/secrets/gh/retire").status_code == 200


def test_the_door_is_recorded_per_header_value(hub: HubHarness) -> None:
    for i, door in enumerate(["cli", "board", "apply", "migration", "nonsense", None]):
        headers = {"X-Blizzard-Door": door} if door else {}
        resp = hub.client.post(
            "/api/work-sources", json={**_DEMO, "name": f"s{i}", "locator": f"acme/s{i}"}, headers=headers
        )
        assert resp.status_code == 201
    doors = {c["record_key"]: c["door"] for c in _changes(hub, record_kind="work_source")}
    assert doors == {"s0": "cli", "s1": "board", "s2": "api", "s3": "api", "s4": "api", "s5": "api"}
    secret_rows = _changes(hub, record_kind="secret")
    assert [(c["op"], c["door"], c["actor"]) for c in secret_rows] == [("create", "api", "operator")]


def test_the_change_log_pages_newest_first_and_filters(hub: HubHarness) -> None:
    _create(hub)
    hub.client.patch("/api/work-sources/demo", json={"annotate": False})
    full = hub.client.get("/api/config/changes").json()
    assert [c["op"] for c in full["changes"]] == ["edit", "create", "create"]
    assert full["next_before"] is None
    edit = full["changes"][0]
    assert edit["diff"] == [{"field": "annotate", "old": True, "new": False}]
    assert (edit["record_kind"], edit["record_key"], edit["revision"], edit["actor"]) == (
        "work_source",
        "demo",
        2,
        "operator",
    )

    first = hub.client.get("/api/config/changes", params={"limit": 2}).json()
    assert len(first["changes"]) == 2
    assert first["next_before"] == first["changes"][-1]["id"]
    rest = hub.client.get("/api/config/changes", params={"limit": 2, "before": first["next_before"]}).json()
    assert [c["op"] for c in rest["changes"]] == ["create"]
    assert rest["next_before"] is None
    assert len(_changes(hub, record_key="gh")) == 1
    assert hub.client.get("/api/config/changes", params={"limit": 201}).status_code == 422
    assert hub.client.get("/api/config/changes", params={"record_kind": "bogus"}).status_code == 422


def test_the_schema_endpoint_describes_the_document_model(hub: HubHarness) -> None:
    schema = hub.client.get("/api/config/schema/work-sources")
    assert schema.status_code == 200
    body = schema.json()
    assert set(_DEMO) <= set(body["properties"])
    assert body["required"] == ["name", "provider", "locator"]
    assert body["additionalProperties"] is False
    assert hub.client.get("/api/config/schema/repositories").status_code == 404


def test_no_secret_value_reaches_a_change_row_or_the_log_route(hub: HubHarness) -> None:
    _create(hub)
    hub.client.put("/api/secrets/gh/value", json={"value": _SENTINEL + "-2"})
    hub.client.post("/api/secrets/gh/retire")
    hub.client.post("/api/secrets/gh/enable")
    text = hub.client.get("/api/config/changes").text
    assert "gh" in text, "positive control: the scan sees the secret's name"
    assert _SENTINEL not in text
    with hub.engine.connect() as conn:
        dump = repr(conn.exec_driver_sql("SELECT * FROM config_changes").all())
    assert "gh" in dump
    assert _SENTINEL not in dump


def test_writes_need_config_edit_and_reads_need_fleet_view(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = {"Authorization": f"Bearer {seed_session(hub, seed_user(hub, username='ada', role=Role.ADMIN))}"}
    contributor = {
        "Authorization": f"Bearer {seed_session(hub, seed_user(hub, username='con', role=Role.CONTRIBUTOR))}"
    }
    guest = {"Authorization": f"Bearer {seed_session(hub, seed_user(hub, username='gus', role=Role.GUEST))}"}
    hub.client.post("/api/secrets", json={"name": "gh", "value": "v"}, headers=admin)

    assert hub.client.post("/api/work-sources", json=_DEMO).status_code == 401
    assert hub.client.post("/api/work-sources", json=_DEMO, headers=contributor).status_code == 403
    created = hub.client.post("/api/work-sources", json=_DEMO, headers=admin)
    assert created.status_code == 201
    for path in ("/api/work-sources/demo/retire", "/api/work-sources/demo/enable"):
        assert hub.client.post(path, headers=contributor).status_code == 403
    assert hub.client.patch("/api/work-sources/demo", json={}, headers=contributor).status_code == 403
    for path in (
        "/api/work-sources",
        "/api/work-sources/demo",
        "/api/config/changes",
        "/api/config/schema/work-sources",
    ):
        assert hub.client.get(path, headers=guest).status_code == 200, path
        assert hub.client.get(path).status_code == 401, path
    row = hub.client.get("/api/config/changes", headers=guest).json()["changes"][0]
    assert row["actor"] != "operator"
