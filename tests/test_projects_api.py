"""The project routes over the hub app: a project is created, listed, read and edited under the configured-record
verbs and never retired; a slug it left keeps answering for it, without a redirect; its source and repository links
are linked, unlinked by retiring and relinked, each change carrying the project's id into the change log; the built-in
``hub`` source is listed as linked and cannot be unlinked; reads take ``FLEET_VIEW`` and writes ``CONFIG_EDIT``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from blizzard.auth_core import ROLE_PERMISSIONS, Permission, Role
from blizzard.hub.auth.memberships import MEMBERSHIP_ROLES
from tests.support import HubHarness, build_hub, seed_session, seed_user

pytestmark = pytest.mark.component


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    return build_hub(tmp_path, link=False)


def _ok(response, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return response.json()


def _create(client: TestClient, slug: str, name: str | None = None, **extra: object) -> dict[str, Any]:
    return _ok(client.post("/api/projects", json={"slug": slug, "name": name or slug.title(), **extra}), 201)


def _seed_records(client: TestClient) -> str:
    """A secret, the work source ``acme`` and the repository ``acme-repo`` that name it; the source's id."""
    _ok(client.post("/api/secrets", json={"name": "token", "value": "v"}), 201)
    source = {"slug": "acme", "provider": "github", "locator": "acme/widgets", "secret": "token"}
    source_id = _ok(client.post("/api/work-sources", json=source), 201)["work_source_id"]
    repository = {
        "slug": "acme-repo",
        "forge_api_url": "https://forge.example",
        "owner": "acme",
        "repo": "widgets",
        "base_branch": "main",
        "secret_name": "token",
    }
    _ok(client.post("/api/repositories", json=repository), 201)
    return source_id


def _changes(client: TestClient, **params: str) -> list[dict[str, Any]]:
    return _ok(client.get("/api/config/changes", params=params))["changes"]


# --- Projects ----------------------------------------------------------------------------------------------------


def test_a_created_project_is_listed_by_slug_and_read_by_its_id_or_slug(hub: HubHarness) -> None:
    created = _create(hub.client, "widgets", "Widgets", description="the widget line")

    assert created["project_id"].startswith("proj_")
    assert (created["slug"], created["name"], created["description"], created["revision"]) == (
        "widgets",
        "Widgets",
        "the widget line",
        1,
    )
    listed = _ok(hub.client.get("/api/projects"))["projects"]
    assert [project["slug"] for project in listed] == ["default", "widgets"]
    assert _ok(hub.client.get(f"/api/projects/{created['project_id']}")) == created
    assert _ok(hub.client.get("/api/projects/widgets")) == created


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"slug": "Widgets", "name": "W"}, "slug"),
        ({"slug": "two--hyphens", "name": "W"}, "slug"),
        ({"slug": "widgets", "name": " padded"}, "name"),
        ({"slug": "widgets", "name": ""}, "name"),
    ],
)
def test_a_malformed_slug_or_name_answers_422_naming_the_field(
    hub: HubHarness, body: dict[str, Any], field: str
) -> None:
    response = hub.client.post("/api/projects", json=body)

    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["loc"] == ["body", field]
    assert [project["slug"] for project in _ok(hub.client.get("/api/projects"))["projects"]] == ["default"]


def test_a_taken_slug_answers_409_and_an_unknown_field_422(hub: HubHarness) -> None:
    taken = hub.client.post("/api/projects", json={"slug": "default", "name": "Again"})
    unknown = hub.client.post("/api/projects", json={"slug": "widgets", "name": "W", "retired": True})

    assert taken.status_code == 409, taken.text
    assert "default" in taken.json()["detail"]
    assert unknown.status_code == 422, unknown.text


def test_a_patch_edits_the_fields_present_and_a_moved_slug_still_answers_without_a_redirect(hub: HubHarness) -> None:
    created = _create(hub.client, "widgets", "Widgets")
    project_id = created["project_id"]

    moved = _ok(hub.client.patch("/api/projects/widgets", json={"slug": "gadgets", "name": "Gadgets"}))

    assert (moved["project_id"], moved["slug"], moved["name"], moved["revision"]) == (
        project_id,
        "gadgets",
        "Gadgets",
        2,
    )
    via_former = hub.client.get("/api/projects/widgets", follow_redirects=False)
    assert via_former.status_code == 200
    assert via_former.json() == moved
    assert _ok(hub.client.get(f"/api/projects/{project_id}")) == moved
    assert _ok(hub.client.patch("/api/projects/widgets", json={"description": "now"}))["description"] == "now"


def test_a_former_slug_answers_for_the_project_that_took_it_once_one_does(hub: HubHarness) -> None:
    moved = _create(hub.client, "widgets")
    _ok(hub.client.patch(f"/api/projects/{moved['project_id']}", json={"slug": "gadgets"}))

    taker = _create(hub.client, "widgets", "New widgets")

    assert _ok(hub.client.get("/api/projects/widgets"))["project_id"] == taker["project_id"]
    assert _ok(hub.client.get("/api/projects/gadgets"))["project_id"] == moved["project_id"]


def test_a_patch_that_changes_nothing_writes_nothing(hub: HubHarness) -> None:
    created = _create(hub.client, "widgets", "Widgets")
    before = _changes(hub.client, project="widgets")

    same = _ok(hub.client.patch("/api/projects/widgets", json={"name": "Widgets"}))

    assert same == created
    assert _changes(hub.client, project="widgets") == before


def test_a_stale_revision_answers_409_naming_the_current_one_and_writes_nothing(hub: HubHarness) -> None:
    _create(hub.client, "widgets")
    _ok(hub.client.patch("/api/projects/widgets", json={"name": "Renamed"}))

    stale = hub.client.patch("/api/projects/widgets", json={"name": "Stale"}, headers={"If-Match": "1"})

    assert stale.status_code == 409, stale.text
    assert "2" in stale.json()["detail"]
    assert _ok(hub.client.get("/api/projects/widgets"))["name"] == "Renamed"


@pytest.mark.parametrize("field", ["slug", "name", "description"])
def test_an_explicit_null_answers_422_naming_the_field(hub: HubHarness, field: str) -> None:
    _create(hub.client, "widgets")

    response = hub.client.patch("/api/projects/widgets", json={field: None})

    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["loc"] == ["body", field]


def test_a_slug_another_project_holds_answers_409_on_a_patch(hub: HubHarness) -> None:
    _create(hub.client, "widgets")

    response = hub.client.patch("/api/projects/widgets", json={"slug": "default"})

    assert response.status_code == 409, response.text


def test_the_default_project_is_edited_like_any_other_and_no_project_is_retired(hub: HubHarness) -> None:
    renamed = _ok(hub.client.patch("/api/projects/default", json={"slug": "main", "name": "Main"}))

    assert (renamed["slug"], renamed["name"]) == ("main", "Main")
    assert hub.client.post("/api/projects/main/retire").status_code in (404, 405)
    assert hub.client.delete("/api/projects/main").status_code == 405


@pytest.mark.parametrize("value", ["nope", "proj_01JZZZZZZZZZZZZZZZZZZZZZZZ", "Default", "Widgets Line"])
def test_a_path_naming_no_project_answers_404(hub: HubHarness, value: str) -> None:
    _create(hub.client, "widgets", "Widgets Line")

    for path in (f"/api/projects/{value}", f"/api/projects/{value}/source-links"):
        response = hub.client.get(path)
        assert response.status_code == 404, f"{path}: {response.text}"


# --- Links -------------------------------------------------------------------------------------------------------


def test_a_source_link_is_linked_unlinked_by_retiring_and_relinked_each_change_carrying_the_project(
    hub: HubHarness,
) -> None:
    source_id = _seed_records(hub.client)
    project_id = _create(hub.client, "widgets")["project_id"]

    linked = _ok(hub.client.put("/api/projects/widgets/source-links/acme"))
    unlinked = _ok(hub.client.post("/api/projects/widgets/source-links/acme/retire"))
    relinked = _ok(hub.client.put(f"/api/projects/{project_id}/source-links/{source_id}"))

    assert [(v["name"], v["slug"], v["linked"], v["revision"]) for v in (linked, unlinked, relinked)] == [
        (source_id, "acme", True, 1),
        (source_id, "acme", False, 2),
        (source_id, "acme", True, 3),
    ]
    link_changes = [c for c in _changes(hub.client, project="widgets") if c["record_kind"] == "project_source_link"]
    assert [(c["record_key"], c["record_label"], c["op"], c["project_id"]) for c in link_changes] == [
        (source_id, "acme", "enable", project_id),
        (source_id, "acme", "retire", project_id),
        (source_id, "acme", "create", project_id),
    ]


def test_linking_a_standing_link_and_unlinking_an_unlinked_one_change_nothing(hub: HubHarness) -> None:
    _seed_records(hub.client)
    _create(hub.client, "widgets")
    first = _ok(hub.client.put("/api/projects/widgets/source-links/acme"))

    assert _ok(hub.client.put("/api/projects/widgets/source-links/acme")) == first
    retired = _ok(hub.client.post("/api/projects/widgets/source-links/acme/retire"))
    assert _ok(hub.client.post("/api/projects/widgets/source-links/acme/retire")) == retired
    assert len(_changes(hub.client, project="widgets", record_kind="project_source_link")) == 2


def test_the_source_links_listing_reports_the_built_in_hub_source_as_linked(hub: HubHarness) -> None:
    source_id = _seed_records(hub.client)
    project_id = _create(hub.client, "widgets")["project_id"]
    _ok(hub.client.put("/api/projects/widgets/source-links/acme"))

    listed = _ok(hub.client.get("/api/projects/widgets/source-links"))["source_links"]

    assert listed == [
        {"project_id": project_id, "name": source_id, "slug": "acme", "linked": True, "revision": 1, "built_in": False},
        {"project_id": project_id, "name": "hub", "slug": "hub", "linked": True, "revision": None, "built_in": True},
    ]


def test_a_renamed_source_keeps_its_link_and_answers_to_its_former_slug(hub: HubHarness) -> None:
    source_id = _seed_records(hub.client)
    _create(hub.client, "widgets")
    _ok(hub.client.put("/api/projects/widgets/source-links/acme"))
    _ok(hub.client.patch("/api/work-sources/acme", json={"slug": "acme-core", "name": "Acme core"}))

    listed = _ok(hub.client.get("/api/projects/widgets/source-links"))["source_links"]
    assert [(link["name"], link["slug"]) for link in listed] == [(source_id, "acme-core"), ("hub", "hub")]
    unlinked = _ok(hub.client.post("/api/projects/widgets/source-links/acme/retire"))
    assert (unlinked["name"], unlinked["slug"], unlinked["linked"]) == (source_id, "acme-core", False)


def test_an_unlinked_source_leaves_the_listing(hub: HubHarness) -> None:
    _seed_records(hub.client)
    _create(hub.client, "widgets")
    _ok(hub.client.put("/api/projects/widgets/source-links/acme"))
    _ok(hub.client.post("/api/projects/widgets/source-links/acme/retire"))

    listed = _ok(hub.client.get("/api/projects/widgets/source-links"))["source_links"]

    assert [link["slug"] for link in listed] == ["hub"]


def test_unlinking_the_hub_source_answers_409_and_linking_it_changes_nothing(hub: HubHarness) -> None:
    project_id = _create(hub.client, "widgets")["project_id"]
    before = _changes(hub.client)

    linked = _ok(hub.client.put("/api/projects/widgets/source-links/hub"))
    refused = hub.client.post("/api/projects/widgets/source-links/hub/retire")

    assert linked == {
        "project_id": project_id,
        "name": "hub",
        "slug": "hub",
        "linked": True,
        "revision": None,
        "built_in": True,
    }
    assert refused.status_code == 409, refused.text
    assert "built in" in refused.json()["detail"]
    assert _changes(hub.client) == before


def test_a_link_to_an_unknown_record_or_never_linked_answers_404(hub: HubHarness) -> None:
    _seed_records(hub.client)
    _create(hub.client, "widgets")

    for method, path in (
        ("PUT", "/api/projects/widgets/source-links/nope"),
        ("PUT", "/api/projects/widgets/repository-links/nope"),
        ("POST", "/api/projects/widgets/source-links/acme/retire"),
        ("POST", "/api/projects/widgets/repository-links/acme-repo/retire"),
        ("PUT", "/api/projects/nope/source-links/acme"),
        ("PUT", "/api/projects/nope/repository-links/acme-repo"),
    ):
        response = hub.client.request(method, path)
        assert response.status_code == 404, f"{method} {path}: {response.text}"


def test_a_retired_record_may_be_linked(hub: HubHarness) -> None:
    _seed_records(hub.client)
    _create(hub.client, "widgets")
    _ok(hub.client.post("/api/work-sources/acme/retire"))
    _ok(hub.client.post("/api/repositories/acme-repo/retire"))

    assert _ok(hub.client.put("/api/projects/widgets/source-links/acme"))["linked"] is True
    assert _ok(hub.client.put("/api/projects/widgets/repository-links/acme-repo"))["linked"] is True


def test_a_repository_link_is_linked_listed_unlinked_and_relinked(hub: HubHarness) -> None:
    _seed_records(hub.client)
    project_id = _create(hub.client, "widgets")["project_id"]

    linked = _ok(hub.client.put("/api/projects/widgets/repository-links/acme-repo"))
    listed = _ok(hub.client.get("/api/projects/widgets/repository-links"))["repository_links"]
    unlinked = _ok(hub.client.post("/api/projects/widgets/repository-links/acme-repo/retire"))
    after = _ok(hub.client.get("/api/projects/widgets/repository-links"))["repository_links"]
    relinked = _ok(hub.client.put("/api/projects/widgets/repository-links/acme-repo"))

    assert listed == [linked]
    repository_id = hub.client.get("/api/repositories/acme-repo").json()["repository_id"]
    assert linked == {
        "project_id": project_id,
        "repository_id": repository_id,
        "slug": "acme-repo",
        "name": "acme-repo",
        "linked": True,
        "revision": 1,
        "position": None,
    }
    assert (unlinked["linked"], unlinked["revision"]) == (False, 2)
    assert after == []
    assert (relinked["linked"], relinked["revision"]) == (True, 3)
    changes = _changes(hub.client, project=project_id, record_kind="project_repository_link")
    assert [(c["op"], c["project_id"]) for c in changes] == [
        ("enable", project_id),
        ("retire", project_id),
        ("create", project_id),
    ]
    assert {(c["record_key"], c["record_label"]) for c in changes} == {(repository_id, "acme-repo")}


def test_a_stale_revision_on_a_link_answers_409(hub: HubHarness) -> None:
    _seed_records(hub.client)
    _create(hub.client, "widgets")
    _ok(hub.client.put("/api/projects/widgets/repository-links/acme-repo"))

    stale = hub.client.post("/api/projects/widgets/repository-links/acme-repo/retire", headers={"If-Match": "7"})

    assert stale.status_code == 409, stale.text
    assert _ok(hub.client.get("/api/projects/widgets/repository-links"))["repository_links"][0]["linked"] is True


def test_a_link_belongs_to_its_project_alone(hub: HubHarness) -> None:
    _seed_records(hub.client)
    _create(hub.client, "widgets")
    _ok(hub.client.put("/api/projects/widgets/source-links/acme"))

    other = _ok(hub.client.get("/api/projects/default/source-links"))["source_links"]

    assert [link["slug"] for link in other] == ["hub"]


# --- Permissions -------------------------------------------------------------------------------------------------

_READS = ["/api/projects", "/api/projects/default", "/api/projects/default/source-links"]
_WRITES = [
    ("POST", "/api/projects", {"slug": "widgets", "name": "Widgets"}),
    ("PATCH", "/api/projects/default", {"description": "d"}),
    ("PUT", "/api/projects/default/source-links/hub", None),
    ("POST", "/api/projects/default/repository-links/nope/retire", None),
    ("PUT", "/api/projects/default/landing-order", {"repositories": []}),
    ("POST", "/api/projects/default/landing-order/position", {"repository": "nope", "unranked": True}),
]


@pytest.mark.parametrize("role", MEMBERSHIP_ROLES)
def test_reads_take_fleet_view_and_writes_config_edit(tmp_path: Path, role: Role) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    cookie = {"Cookie": f"bz_session={seed_session(hub, seed_user(hub, username='ada', role=role))}"}
    may_view = Permission.FLEET_VIEW in ROLE_PERMISSIONS[role]
    may_edit = Permission.CONFIG_EDIT in ROLE_PERMISSIONS[role]

    for path in _READS:
        assert (hub.client.get(path, headers=cookie).status_code == 200) is may_view, f"{role} GET {path}"
    for method, path, body in _WRITES:
        status = hub.client.request(method, path, json=body, headers=cookie).status_code
        assert (status != 403) is may_edit, f"{role} {method} {path}: {status}"
    assert may_edit is (role is Role.ADMIN)


# --- Color -------------------------------------------------------------------------------------------------------


def test_a_create_without_a_color_takes_the_least_used_one_and_a_stated_color_is_stored(hub: HubHarness) -> None:
    default = _ok(hub.client.get("/api/projects/default"))
    first = _create(hub.client, "first")
    stated = _create(hub.client, "stated", color="slate")

    assert first["color"] != default["color"]
    assert stated["color"] == "slate"
    assert _ok(hub.client.get("/api/projects/stated"))["color"] == "slate"


def test_a_patch_sets_the_color_bumps_the_revision_and_logs_the_field(hub: HubHarness) -> None:
    created = _create(hub.client, "widgets", color="red")

    edited = _ok(hub.client.patch("/api/projects/widgets", json={"color": "teal"}, headers={"If-Match": "1"}))

    assert (edited["color"], edited["revision"]) == ("teal", 2)
    diffs = [field for change in _changes(hub.client, project=created["project_id"]) for field in change["diff"]]
    assert any(field["field"] == "color" and (field["old"], field["new"]) == ("red", "teal") for field in diffs)


@pytest.mark.parametrize("method", ["post", "patch"])
def test_a_color_outside_the_palette_answers_422_on_color_listing_the_palette(hub: HubHarness, method: str) -> None:
    _create(hub.client, "widgets")
    if method == "post":
        response = hub.client.post("/api/projects", json={"slug": "other", "name": "Other", "color": "mauve"})
    else:
        response = hub.client.patch("/api/projects/widgets", json={"color": "mauve"})

    assert response.status_code == 422, response.text
    detail = response.json()["detail"][0]
    assert detail["loc"] == ["body", "color"]
    assert all(name in detail["msg"] for name in ("red", "orange", "amber", "lime", "green", "teal", "slate"))


def test_a_hex_color_is_stored_lowercase_on_create_and_edit_and_an_unchanged_edit_writes_nothing(
    hub: HubHarness,
) -> None:
    created = _create(hub.client, "widgets", color="#14B8A6")
    same = _ok(hub.client.patch("/api/projects/widgets", json={"color": "#14b8a6"}))
    edited = _ok(hub.client.patch("/api/projects/widgets", json={"color": "#FF8800"}))

    assert (created["color"], same["revision"], edited["color"], edited["revision"]) == ("#14b8a6", 1, "#ff8800", 2)
    assert _ok(hub.client.get("/api/projects/widgets"))["color"] == "#ff8800"


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize("raw", ["#abc", "#12345g", "14b8a6"])
def test_a_malformed_hex_color_answers_422_on_color_naming_both_forms(hub: HubHarness, method: str, raw: str) -> None:
    _create(hub.client, "widgets")
    if method == "post":
        response = hub.client.post("/api/projects", json={"slug": "other", "name": "Other", "color": raw})
    else:
        response = hub.client.patch("/api/projects/widgets", json={"color": raw})

    assert response.status_code == 422, response.text
    detail = response.json()["detail"][0]
    assert detail["loc"] == ["body", "color"]
    assert "teal" in detail["msg"] and "#rrggbb" in detail["msg"]


def test_an_explicit_null_color_answers_422(hub: HubHarness) -> None:
    _create(hub.client, "widgets")

    response = hub.client.patch("/api/projects/widgets", json={"color": None})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "color"]
