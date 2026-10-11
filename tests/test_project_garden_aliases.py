"""The un-nested by-slug gardening routes and creates stay, as deprecated aliases acting in the call's project
(component tier): on a tenant holding one project each one serves it; on a tenant holding several each one is
refused ``422 project_required``, listing the projects to choose from."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import HubHarness, build_hub

pytestmark = pytest.mark.component


def _calls(graph_name: str) -> list[tuple[str, str, dict[str, object] | None]]:
    return [
        ("POST", "/api/scopes", {"slug": "blizzard"}),
        ("GET", "/api/scopes/blizzard", None),
        ("PATCH", "/api/scopes/blizzard", {"description": "d"}),
        ("GET", "/api/scopes/blizzard/routines", None),
        ("POST", "/api/scopes/blizzard/retire", {}),
        ("POST", "/api/scopes/blizzard/enable", {}),
        ("POST", "/api/routines", {"name": "nightly", "graph_name": graph_name, "default_scope_slug": "blizzard"}),
        ("POST", "/api/garden-proposals", {"class": "c", "title": "t", "body": "b", "findings": []}),
    ]


def _graph_name(hub: HubHarness) -> str:
    return hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    ).name


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    return build_hub(tmp_path)


def test_every_alias_serves_a_tenant_holding_one_project(hub: HubHarness) -> None:
    for method, path, body in _calls(_graph_name(hub)):
        response = hub.client.request(method, path, json=body)
        assert response.status_code < 300, (method, path, response.text)
        if isinstance(response.json(), dict):
            assert response.json()["project_slug"] == "default", (method, path)


def test_every_alias_is_refused_on_a_tenant_holding_two_projects(hub: HubHarness) -> None:
    graph_name = _graph_name(hub)
    for method, path, body in _calls(graph_name)[:1]:
        assert hub.client.request(method, path, json=body).status_code == 201
    widgets = hub.client.post("/api/projects", json={"slug": "widgets", "name": "Widgets"}).json()

    for method, path, body in _calls(graph_name):
        response = hub.client.request(method, path, json=body)
        assert response.status_code == 422, (method, path, response.text)
        detail = response.json()["detail"]
        assert detail["code"] == "project_required", (method, path)
        assert sorted(p["slug"] for p in detail["projects"]) == ["default", "widgets"]
    assert widgets["slug"] == "widgets"
