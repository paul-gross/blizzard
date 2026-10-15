"""The project a call names, resolved at the edge: an id whatever the project's slug, a current slug, then a slug a
project left; never a display name. An unresolved project answers ``404`` on a path and ``422`` in a query or body,
the ``422`` naming where. The change log narrows to one project's changes by ``?project=``, each entry carrying the
project it concerns, and a call that needs a project while the tenant holds several is refused with the projects to
choose from."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from blizzard.hub.api.project_params import UNKNOWN_PROJECT, body_project
from tests.support import HubHarness, build_hub, default_project

pytestmark = pytest.mark.component


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    return build_hub(tmp_path)


def _ok(response, status: int = 200) -> dict:  # type: ignore[no-untyped-def]
    assert response.status_code == status, response.text
    return response.json()


def _create(hub: HubHarness, slug: str, name: str) -> str:
    return _ok(hub.client.post("/api/projects", json={"slug": slug, "name": name}), 201)["project_id"]


def _change_keys(hub: HubHarness, project: str) -> list[tuple[str, str, str | None]]:
    changes = _ok(hub.client.get("/api/config/changes", params={"project": project}))["changes"]
    return [(c["record_kind"], c["record_key"], c["project_id"]) for c in changes if c["project_id"] is not None]


def test_the_change_log_narrows_to_one_project_named_by_id_slug_or_former_slug(hub: HubHarness) -> None:
    widgets = _create(hub, "widgets", "Widgets")
    _ok(hub.client.post("/api/secrets", json={"name": "token", "value": "v"}), 201)
    _ok(hub.client.patch("/api/projects/widgets", json={"slug": "gadgets"}))

    expected = [("project", widgets, widgets), ("project", widgets, widgets)]
    assert _change_keys(hub, widgets) == expected
    assert _change_keys(hub, "gadgets") == expected
    assert _change_keys(hub, "widgets") == expected
    every = _ok(hub.client.get("/api/config/changes"))["changes"]
    assert {c["record_kind"]: c["project_id"] for c in every}["secret"] is None
    assert len(every) > len(expected)
    lensed = _ok(hub.client.get("/api/config/changes", params={"project": widgets}))["changes"]
    assert "secret" in {c["record_kind"] for c in lensed}, "the lens keeps the tenant-wide changes"


@pytest.mark.parametrize("value", ["nope", "Widgets", "proj_01JZZZZZZZZZZZZZZZZZZZZZZZ", "default slug"])
def test_a_query_naming_no_project_answers_422_naming_the_parameter(hub: HubHarness, value: str) -> None:
    _create(hub, "widgets", "Widgets")

    response = hub.client.get("/api/config/changes", params={"project": value})

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == [
        {"loc": ["query", "project"], "msg": f"unknown project {value!r}", "type": UNKNOWN_PROJECT}
    ]


def test_an_id_resolves_to_its_project_whatever_slug_it_holds(hub: HubHarness) -> None:
    default = default_project(hub.engine)
    _ok(hub.client.patch(f"/api/projects/{default}", json={"slug": "main"}))
    _create(hub, "default", "Taken")

    assert _ok(hub.client.get(f"/api/projects/{default}"))["slug"] == "main"
    assert _ok(hub.client.get("/api/projects/default"))["name"] == "Taken"


def test_a_body_value_resolves_by_id_or_slug_and_naming_none_is_422_on_its_field(hub: HubHarness) -> None:
    widgets = _create(hub, "widgets", "Widgets")

    assert body_project(widgets, hub.services).slug == "widgets"
    assert body_project("widgets", hub.services).project_id == widgets
    with pytest.raises(HTTPException) as refused:
        body_project("Widgets", hub.services, field="projects")
    assert refused.value.status_code == 422
    assert refused.value.detail == [
        {"loc": ["body", "projects"], "msg": "unknown project 'Widgets'", "type": UNKNOWN_PROJECT}
    ]


def test_a_call_needing_a_project_while_the_tenant_holds_two_lists_them(hub: HubHarness) -> None:
    default = default_project(hub.engine)
    widgets = _create(hub, "widgets", "Widgets")

    response = hub.client.get("/api/findings", params={"routine": "r", "scope": "s"})

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert (detail["code"], detail["message"]) == (
        "project_required",
        "name a project (this tenant holds: default, widgets)",
    )
    assert detail["projects"] == [
        {"project_id": default, "slug": "default", "name": "Default"},
        {"project_id": widgets, "slug": "widgets", "name": "Widgets"},
    ]
