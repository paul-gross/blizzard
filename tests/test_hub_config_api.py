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
    hub = build_hub(tmp_path, repositories=())
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
    repositories = hub.client.get("/api/config/schema/repositories")
    assert repositories.status_code == 200
    assert repositories.json()["required"] == [
        "name",
        "forge_api_url",
        "owner",
        "repo",
        "base_branch",
        "secret_name",
    ]
    assert repositories.json()["additionalProperties"] is False
    assert hub.client.get("/api/config/schema/nope").status_code == 404


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
    hub = build_hub(tmp_path, auth_mode="oauth", repositories=())
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


# --- Repositories ------------------------------------------------------------------

_REPO = {
    "name": "blizzard",
    "forge_api_url": "https://api.github.com",
    "owner": "acme",
    "repo": "blizzard",
    "base_branch": "master",
    "secret_name": "gh",
}


def _create_repo(hub: HubHarness, **overrides: object):  # type: ignore[no-untyped-def]
    return hub.client.post("/api/repositories", json={**_REPO, **overrides})


def test_a_repository_creates_shows_and_lists(hub: HubHarness) -> None:
    created = _create_repo(hub)
    assert created.status_code == 201, created.text
    body = created.json()
    assert {k: body[k] for k in _REPO} == _REPO
    assert (body["revision"], body["retired"], body["created_by"]) == (1, False, "operator")
    assert hub.client.get("/api/repositories/blizzard").json() == body
    assert hub.client.get("/api/repositories").json()["repositories"] == [body]
    assert hub.client.get("/api/repositories/missing").status_code == 404


def test_the_repository_create_422s_name_the_offending_field(hub: HubHarness) -> None:
    for overrides, field in (
        ({"name": " "}, "name"),
        ({"owner": ""}, "owner"),
        ({"forge_api_url": "ftp://forge"}, "forge_api_url"),
        ({"forge_api_url": "api.github.com"}, "forge_api_url"),
        ({"secret_name": "absent"}, "secret_name"),
        ({"secret_name": None}, "secret_name"),
    ):
        resp = _create_repo(hub, **overrides)
        assert resp.status_code == 422, (overrides, resp.text)
        assert resp.json()["detail"][0]["loc"][-1] == field, overrides
    assert hub.client.post("/api/repositories", json={**_REPO, "bogus": 1}).status_code == 422
    assert _changes(hub, record_kind="repository") == []


def test_a_taken_repository_name_or_coordinate_is_a_409(hub: HubHarness) -> None:
    assert _create_repo(hub).status_code == 201
    assert _create_repo(hub).status_code == 409
    hub.client.post("/api/repositories/blizzard/retire")
    held = _create_repo(hub, name="other")
    assert held.status_code == 409
    assert "repository blizzard" in held.json()["detail"]
    assert _create_repo(hub, name="other", forge_api_url="https://ghe.example/api/v3").status_code == 201


def test_a_repository_patch_is_sparse_and_refuses_every_null(hub: HubHarness) -> None:
    _create_repo(hub)
    assert hub.client.patch("/api/repositories/blizzard", json={}).json()["revision"] == 1
    assert hub.client.patch("/api/repositories/blizzard", json={"owner": "acme"}).json()["revision"] == 1
    edited = hub.client.patch("/api/repositories/blizzard", json={"base_branch": "main"}, headers={"If-Match": "1"})
    assert (edited.json()["revision"], edited.json()["base_branch"], edited.json()["owner"]) == (2, "main", "acme")
    for field in ("forge_api_url", "owner", "repo", "base_branch", "secret_name"):
        resp = hub.client.patch("/api/repositories/blizzard", json={field: None})
        assert resp.status_code == 422, field
        assert resp.json()["detail"][0]["loc"][-1] == field
    assert hub.client.patch("/api/repositories/blizzard", json={"name": "other"}).status_code == 422
    assert hub.client.patch("/api/repositories/blizzard", json={"secret_name": "absent"}).status_code == 422
    assert hub.client.patch("/api/repositories/nope", json={}).status_code == 404
    for call in (
        lambda h: hub.client.patch("/api/repositories/blizzard", json={"base_branch": "dev"}, headers=h),
        lambda h: hub.client.post("/api/repositories/blizzard/retire", headers=h),
        lambda h: hub.client.post("/api/repositories/blizzard/enable", headers=h),
    ):
        stale = call({"If-Match": "1"})
        assert stale.status_code == 409
        assert "revision 2" in stale.json()["detail"]


def test_repository_retire_and_enable_move_the_revision_and_guard_the_secret(hub: HubHarness) -> None:
    _create_repo(hub)
    assert hub.client.get("/api/secrets/gh").json()["references"] == [{"kind": "repository", "key": "blizzard"}]
    refused = hub.client.post("/api/secrets/gh/retire")
    assert refused.status_code == 409
    assert "repository blizzard" in refused.json()["detail"]

    retired = hub.client.post("/api/repositories/blizzard/retire").json()
    assert (retired["revision"], retired["retired"]) == (2, True)
    assert hub.client.get("/api/repositories").json()["repositories"] == []
    assert len(hub.client.get("/api/repositories", params={"include_retired": True}).json()["repositories"]) == 1
    assert hub.client.post("/api/secrets/gh/retire").status_code == 200
    enabled = hub.client.post("/api/repositories/blizzard/enable")
    assert enabled.status_code == 422
    assert enabled.json()["detail"][0]["loc"][-1] == "secret_name"
    assert [c["op"] for c in _changes(hub, record_kind="repository")] == ["retire", "create"]


def test_repository_writes_need_config_edit_and_reads_need_fleet_view(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth", repositories=())
    admin = {"Authorization": f"Bearer {seed_session(hub, seed_user(hub, username='ada', role=Role.ADMIN))}"}
    contributor = {
        "Authorization": f"Bearer {seed_session(hub, seed_user(hub, username='con', role=Role.CONTRIBUTOR))}"
    }
    guest = {"Authorization": f"Bearer {seed_session(hub, seed_user(hub, username='gus', role=Role.GUEST))}"}
    hub.client.post("/api/secrets", json={"name": "gh", "value": "v"}, headers=admin)

    assert hub.client.post("/api/repositories", json=_REPO).status_code == 401
    assert hub.client.post("/api/repositories", json=_REPO, headers=contributor).status_code == 403
    assert (
        hub.client.post("/api/repositories", json=_REPO, headers={**admin, "X-Blizzard-Door": "cli"}).status_code == 201
    )
    for path in ("/api/repositories/blizzard/retire", "/api/repositories/blizzard/enable"):
        assert hub.client.post(path, headers=contributor).status_code == 403
    assert hub.client.patch("/api/repositories/blizzard", json={}, headers=contributor).status_code == 403
    for path in ("/api/repositories", "/api/repositories/blizzard", "/api/config/schema/repositories"):
        assert hub.client.get(path, headers=guest).status_code == 200, path
        assert hub.client.get(path).status_code == 401, path
    (row,) = hub.client.get("/api/config/changes", params={"record_kind": "repository"}, headers=guest).json()[
        "changes"
    ]
    assert (row["door"], row["actor"] != "operator") == ("cli", True)


# --- Declarative apply and export --------------------------------------------------

_YAML = "application/yaml"
_APPLY_REPO = {
    "name": "blizzard",
    "forge_api_url": "https://api.github.com",
    "owner": "acme",
    "repo": "blizzard",
    "base_branch": "master",
    "secret_name": "gh",
}
_YAML_DOCUMENT = """\
version: 1
secrets: [gh]
work_sources:
  - {name: demo, provider: github, locator: acme/demo, secret: gh, annotate: true}
repositories:
  - {name: blizzard, forge_api_url: "https://api.github.com", owner: acme, repo: blizzard, base_branch: master, secret_name: gh}
"""


def _apply_json(hub: HubHarness, document: dict, *, dry_run: bool = False, headers: dict | None = None):  # type: ignore[no-untyped-def,type-arg]
    return hub.client.post("/api/config/apply", params={"dry_run": dry_run}, json=document, headers=headers or {})


def _apply_yaml(hub: HubHarness, text: str, *, dry_run: bool = False):  # type: ignore[no-untyped-def]
    return hub.client.post(
        "/api/config/apply", params={"dry_run": dry_run}, content=text.encode(), headers={"Content-Type": _YAML}
    )


def test_yaml_and_json_bodies_apply_identically(hub: HubHarness, tmp_path: Path) -> None:
    (tmp_path / "other").mkdir()
    other = build_hub(tmp_path / "other")
    other.client.post("/api/secrets", json={"name": "gh", "value": _SENTINEL})
    document = {
        "version": 1,
        "secrets": ["gh"],
        "work_sources": [_DEMO],
        "repositories": [_APPLY_REPO],
    }
    from_json = _apply_json(hub, document)
    from_yaml = _apply_yaml(other, _YAML_DOCUMENT)
    assert from_json.status_code == 200, from_json.text
    assert from_yaml.status_code == 200, from_yaml.text
    assert from_json.json()["outcomes"] == from_yaml.json()["outcomes"]
    assert [(o["kind"], o["key"], o["op"]) for o in from_json.json()["outcomes"]] == [
        ("work_source", "demo", "create"),
        ("repository", "blizzard", "create"),
    ]
    assert from_json.json()["apply_id"].startswith("apl_")


def test_a_dry_run_writes_nothing_and_matches_the_real_apply(hub: HubHarness) -> None:
    before = _changes(hub)
    dry = _apply_yaml(hub, _YAML_DOCUMENT, dry_run=True)
    assert dry.status_code == 200, dry.text
    assert dry.json()["dry_run"] is True and dry.json()["apply_id"] is None
    assert _changes(hub) == before
    assert hub.client.get("/api/work-sources/demo").status_code == 404
    real = _apply_yaml(hub, _YAML_DOCUMENT)
    assert real.json()["outcomes"] == dry.json()["outcomes"]


def test_the_rows_an_apply_writes_carry_the_apply_door_and_one_apply_id(hub: HubHarness) -> None:
    applied = _apply_yaml(hub, _YAML_DOCUMENT).json()
    rows = [r for r in _changes(hub) if r["door"] == "apply"]
    assert len(rows) == 2
    assert {r["apply_id"] for r in rows} == {applied["apply_id"]}
    assert _apply_yaml(hub, _YAML_DOCUMENT).json()["outcomes"] == [
        {"kind": "work_source", "key": "demo", "op": "unchanged", "diff": []},
        {"kind": "repository", "key": "blizzard", "op": "unchanged", "diff": []},
    ]
    assert len([r for r in _changes(hub) if r["door"] == "apply"]) == 2


def test_a_claimed_apply_door_on_another_route_still_records_api(hub: HubHarness) -> None:
    created = hub.client.post("/api/work-sources", json=_DEMO, headers={"X-Blizzard-Door": "apply"})
    assert created.status_code == 201
    assert _changes(hub, record_key="demo")[0]["door"] == "api"
    applied = _apply_json(
        hub,
        {"version": 1, "work_sources": [{**_DEMO, "annotate": False}]},
        headers={"X-Blizzard-Door": "cli"},
    )
    assert applied.status_code == 200
    assert _changes(hub, record_key="demo")[0]["door"] == "apply"


def test_an_omitted_field_is_left_and_a_stated_one_is_restored(hub: HubHarness) -> None:
    _apply_yaml(hub, _YAML_DOCUMENT)
    hub.client.patch("/api/work-sources/demo", json={"annotate": False})
    partial = {"version": 1, "work_sources": [{"name": "demo", "provider": "github", "locator": "acme/demo"}]}
    assert _apply_json(hub, partial).json()["outcomes"][0]["op"] == "unchanged"
    restored = _apply_json(hub, {"version": 1, "work_sources": [_DEMO]}).json()["outcomes"][0]
    assert (restored["op"], [d["field"] for d in restored["diff"]]) == ("edit", ["annotate"])
    assert hub.client.get("/api/work-sources/demo").json()["annotate"] is True


def test_a_retired_record_is_enabled_and_one_the_document_omits_is_untouched(hub: HubHarness) -> None:
    _apply_yaml(hub, _YAML_DOCUMENT)
    hub.client.post("/api/repositories/blizzard/retire")
    only_source = {"version": 1, "work_sources": [_DEMO]}
    assert _apply_json(hub, only_source).json()["outcomes"][0]["op"] == "unchanged"
    assert hub.client.get("/api/repositories/blizzard").json()["retired"] is True
    again = _apply_json(hub, {"version": 1, "repositories": [_APPLY_REPO]}).json()["outcomes"]
    assert [o["op"] for o in again] == ["enable"]


def test_refusals_carry_their_status_and_entry_and_leave_the_store_unchanged(hub: HubHarness) -> None:
    before = _changes(hub)
    missing = _apply_json(hub, {"version": 1, "secrets": ["nope"], "work_sources": [_DEMO]})
    assert missing.status_code == 422
    assert missing.json()["detail"][0]["loc"] == ["body", "secrets", 0]

    unknown = _apply_json(hub, {"version": 1, "work_sources": [{**_DEMO, "secret": "nope"}]})
    assert unknown.status_code == 422
    assert unknown.json()["detail"][0]["loc"] == ["body", "work_sources", 0, "secret"]

    invalid = _apply_json(hub, {"version": 1, "work_sources": [_DEMO, {**_DEMO, "name": "b", "provider": "x"}]})
    assert invalid.json()["detail"][0]["loc"] == ["body", "work_sources", 1, "provider"]

    shape = _apply_json(hub, {"version": 2})
    assert shape.status_code == 422
    assert shape.json()["detail"][0]["loc"] == ["body", "version"]

    twice = _apply_json(hub, {"version": 1, "work_sources": [_DEMO, _DEMO]})
    assert twice.status_code == 422 and twice.json()["detail"][0]["loc"][:3] == ["body", "work_sources", 1]

    clash = _apply_json(hub, {"version": 1, "work_sources": [_DEMO, {**_DEMO, "name": "other"}]})
    assert clash.status_code == 409 and "work_sources[1]" in clash.json()["detail"]

    built_in = _apply_json(hub, {"version": 1, "work_sources": [{**_DEMO, "name": "hub"}]})
    assert built_in.status_code == 409
    assert _changes(hub) == before
    assert hub.client.get("/api/work-sources/demo").status_code == 404


def test_a_malformed_body_is_422_and_an_unknown_media_type_is_415(hub: HubHarness) -> None:
    broken = hub.client.post("/api/config/apply", content=b"a: [", headers={"Content-Type": _YAML})
    assert broken.status_code == 422
    assert broken.json()["detail"][0]["line"] is not None
    plain = hub.client.post("/api/config/apply", content=b"version: 1", headers={"Content-Type": "text/plain"})
    assert plain.status_code == 415


def test_applying_an_export_writes_nothing(hub: HubHarness) -> None:
    _apply_yaml(hub, _YAML_DOCUMENT)
    hub.client.post("/api/secrets", json={"name": "spare", "value": "v"})
    hub.client.post("/api/secrets/spare/retire")
    hub.client.post("/api/work-sources", json={**_DEMO, "name": "gone", "locator": "acme/gone"})
    hub.client.post("/api/work-sources/gone/retire")
    exported = hub.client.get("/api/config/export")
    assert exported.status_code == 200
    document = exported.json()
    assert document["version"] == 1
    assert document["secrets"] == ["gh"]
    assert [w["name"] for w in document["work_sources"]] == ["demo"]
    assert [r["name"] for r in document["repositories"]] == ["blizzard"]
    assert set(document["work_sources"][0]) == {
        "name",
        "provider",
        "locator",
        "api_base",
        "web_base",
        "annotate",
        "secret",
    }
    before = _changes(hub)
    replay = _apply_json(hub, document)
    assert [o["op"] for o in replay.json()["outcomes"]] == ["unchanged", "unchanged"]
    assert _changes(hub) == before


def test_apply_needs_config_edit_and_export_needs_fleet_view(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = {"Authorization": f"Bearer {seed_session(hub, seed_user(hub, username='ada', role=Role.ADMIN))}"}
    contributor = {
        "Authorization": f"Bearer {seed_session(hub, seed_user(hub, username='con', role=Role.CONTRIBUTOR))}"
    }
    guest = {"Authorization": f"Bearer {seed_session(hub, seed_user(hub, username='gus', role=Role.GUEST))}"}
    document = {"version": 1}
    assert _apply_json(hub, document).status_code == 401
    assert _apply_json(hub, document, headers=contributor).status_code == 403
    assert _apply_json(hub, document, headers=admin).status_code == 200
    assert hub.client.get("/api/config/export").status_code == 401
    assert hub.client.get("/api/config/export", headers=guest).status_code == 200


# --- Scopes and routines in a document ---------------------------------------------

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
_SCOPE_ENTRY = {"slug": "core", "description": "the core"}
_ROUTINE_ENTRY = {"name": "nightly", "graph_name": "alpha", "default_scope_slug": "core", "scopes": ["core", "edge"]}
_GARDEN = {
    "version": 1,
    "scopes": [_SCOPE_ENTRY, {"slug": "edge"}],
    "routines": [_ROUTINE_ENTRY],
}


def _mint_graph(hub: HubHarness, name: str = "alpha") -> None:
    resp = hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH.format(name=name)})
    assert resp.status_code == 201, resp.text


def _rows(applied) -> list[tuple[str, str, str]]:  # type: ignore[no-untyped-def]
    assert applied.status_code == 200, applied.text
    return [(o["kind"], o["key"], o["op"]) for o in applied.json()["outcomes"]]


def _routine(hub: HubHarness, name: str = "nightly") -> dict:  # type: ignore[type-arg]
    return next(r for r in hub.client.get("/api/routines").json() if r["name"] == name)


def test_the_schema_endpoint_serves_scopes_and_routines(hub: HubHarness) -> None:
    scopes = hub.client.get("/api/config/schema/scopes").json()
    assert scopes["required"] == ["slug"] and scopes["additionalProperties"] is False
    routines = hub.client.get("/api/config/schema/routines").json()
    assert routines["required"] == ["name", "graph_name", "default_scope_slug"]
    assert "scopes" in routines["properties"] and routines["additionalProperties"] is False


def test_scopes_and_routines_create_then_stand_unchanged(hub: HubHarness) -> None:
    _mint_graph(hub)
    dry = _apply_json(hub, _GARDEN, dry_run=True)
    assert _rows(dry) == [
        ("scope", "core", "create"),
        ("scope", "edge", "create"),
        ("routine", "nightly", "create"),
        ("routine", "nightly", "edit"),
    ]
    assert hub.client.get("/api/routines").json() == []
    assert hub.client.get("/api/scopes/core").status_code == 404
    real = _apply_json(hub, _GARDEN)
    assert real.json()["outcomes"] == dry.json()["outcomes"]
    routine = _routine(hub)
    assert (routine["default_scope_slug"], routine["revision"]) == ("core", 2)
    assert hub.client.get(f"/api/routines/{routine['routine_id']}/scopes").json() == ["core", "edge"]
    assert hub.client.get("/api/scopes/core").json()["description"] == "the core"
    assert _rows(_apply_json(hub, _GARDEN)) == [
        ("scope", "core", "unchanged"),
        ("scope", "edge", "unchanged"),
        ("routine", "nightly", "unchanged"),
    ]
    rows = [r for r in _changes(hub) if r["record_kind"] in ("scope", "routine")]
    assert {r["door"] for r in rows} == {"apply"} and len(rows) == 4


def test_a_stated_field_is_edited_an_omitted_one_left_and_a_retired_record_enabled(hub: HubHarness) -> None:
    _mint_graph(hub)
    _mint_graph(hub, "beta")
    _apply_json(hub, _GARDEN)
    routine = _routine(hub)
    hub.client.post(f"/api/routines/{routine['routine_id']}/retire", json={})
    hub.client.post("/api/scopes/edge/retire", json={})
    hub.client.patch(f"/api/routines/{routine['routine_id']}", json={"default_effort": "high"})
    document = {
        "version": 1,
        "scopes": [{"slug": "edge", "description": "outer"}],
        "routines": [{"name": "nightly", "graph_name": "beta", "default_scope_slug": "edge", "scopes": ["edge"]}],
    }
    applied = _apply_json(hub, document)
    assert _rows(applied) == [
        ("scope", "edge", "enable"),
        ("scope", "edge", "edit"),
        ("routine", "nightly", "enable"),
        ("routine", "nightly", "edit"),
        ("routine", "nightly", "edit"),
    ]
    edit = applied.json()["outcomes"][3]
    assert [d["field"] for d in edit["diff"]] == ["graph_name", "default_scope_slug"]
    relinked = applied.json()["outcomes"][4]["diff"]
    assert relinked == [{"field": "scopes", "old": ["core", "edge"], "new": ["edge"]}]
    after = _routine(hub)
    assert (after["graph_name"], after["default_effort"], after["retired"]) == ("beta", "high", False)
    assert hub.client.get(f"/api/routines/{routine['routine_id']}/scopes").json() == ["edge"]


def test_an_entry_without_scopes_leaves_the_linked_set(hub: HubHarness) -> None:
    _mint_graph(hub)
    _apply_json(hub, _GARDEN)
    unstated = {"version": 1, "routines": [{k: v for k, v in _ROUTINE_ENTRY.items() if k != "scopes"}]}
    assert _rows(_apply_json(hub, unstated)) == [("routine", "nightly", "unchanged")]
    assert hub.client.get(f"/api/routines/{_routine(hub)['routine_id']}/scopes").json() == ["core", "edge"]


def test_garden_refusals_are_located_at_their_entry_and_leave_the_store_unchanged(hub: HubHarness) -> None:
    _mint_graph(hub)
    before = _changes(hub)
    unknown_default = _apply_json(hub, {"version": 1, "routines": [_ROUTINE_ENTRY]})
    assert unknown_default.status_code == 422
    assert unknown_default.json()["detail"][0]["loc"] == ["body", "routines", 0, "default_scope_slug"]

    unknown_linked = _apply_json(hub, {"version": 1, "scopes": [_SCOPE_ENTRY], "routines": [_ROUTINE_ENTRY]})
    assert unknown_linked.json()["detail"][0]["loc"] == ["body", "routines", 0, "scopes"]

    no_graph = _apply_json(hub, {**_GARDEN, "routines": [{**_ROUTINE_ENTRY, "graph_name": "missing"}]})
    assert no_graph.status_code == 422
    assert no_graph.json()["detail"][0]["loc"] == ["body", "routines", 0, "graph_name"]

    bad_slug = _apply_json(hub, {"version": 1, "scopes": [_SCOPE_ENTRY, {"slug": "Not A Slug"}]})
    assert bad_slug.json()["detail"][0]["loc"] == ["body", "scopes", 1, "slug"]

    twice = _apply_json(hub, {"version": 1, "scopes": [_SCOPE_ENTRY, _SCOPE_ENTRY]})
    assert twice.json()["detail"][0]["loc"][:3] == ["body", "scopes", 1]

    assert _changes(hub) == before
    assert hub.client.get("/api/scopes/core").status_code == 404


def test_an_export_with_scopes_and_routines_applies_back_as_a_no_op(hub: HubHarness) -> None:
    _mint_graph(hub)
    _apply_json(hub, _GARDEN)
    hub.client.post("/api/scopes", json={"slug": "gone"})
    hub.client.post("/api/scopes/gone/retire", json={})
    retired = hub.client.post(
        "/api/routines", json={"name": "old", "graph_name": "alpha", "default_scope_slug": "core"}
    ).json()
    hub.client.post(f"/api/routines/{retired['routine_id']}/retire", json={})
    document = hub.client.get("/api/config/export").json()
    assert document["scopes"] == [{"slug": "core", "description": "the core"}, {"slug": "edge", "description": ""}]
    assert document["routines"] == [
        {
            "name": "nightly",
            "graph_name": "alpha",
            "default_scope_slug": "core",
            "default_model": [],
            "default_effort": None,
            "default_harnesses": [],
            "scopes": ["core", "edge"],
        }
    ]
    before = _changes(hub)
    assert {op for _, _, op in _rows(_apply_json(hub, document))} == {"unchanged"}
    assert _changes(hub) == before
