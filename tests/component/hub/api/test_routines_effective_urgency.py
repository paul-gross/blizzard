"""A routine's view names the urgency a run of it mints at when the run names none (component tier, wired hub app):
the routine's own default when set, else the tenant's — following the tenant's default as it moves — read on get and
list alike, the listing at a statement count flat in the number of routines."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from blizzard.hub.graphs import PACKAGED as PACKAGED_GRAPHS
from blizzard.hub.graphs import packaged_default_yaml
from tests.support.scaffolding import HubHarness, build_hub, count_queries, default_project


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    return build_hub(tmp_path)


def _graph(hub: HubHarness) -> str:
    return hub.services.default_graph.ensure_default(
        PACKAGED_GRAPHS.default.doc, definition_yaml=packaged_default_yaml()
    ).name


def _urgency(hub: HubHarness, slug: str) -> dict[str, Any]:
    created = hub.client.post("/api/urgencies", json={"slug": slug})
    assert created.status_code == 201, created.text
    return created.json()


def _routine(hub: HubHarness, slug: str, **fields: Any) -> dict[str, Any]:
    body = {"slug": slug, "graph_name": _graph(hub), "default_scope_slug": "blizzard", **fields}
    created = hub.client.post(f"/api/projects/{default_project(hub.engine)}/routines", json=body)
    assert created.status_code == 201, created.text
    return created.json()


def _ref(urgency: dict[str, Any]) -> dict[str, str]:
    return {"urgency_id": urgency["urgency_id"], "slug": urgency["slug"]}


def _effective(hub: HubHarness) -> dict[str, dict[str, str]]:
    listed = hub.client.get("/api/routines")
    assert listed.status_code == 200, listed.text
    by_list = {r["slug"]: r["effective_urgency"] for r in listed.json()}
    by_get = {
        r["slug"]: hub.client.get(f"/api/routines/{r['routine_id']}").json()["effective_urgency"] for r in listed.json()
    }
    assert by_get == by_list
    return by_list


def test_a_routine_takes_its_own_default_else_the_tenant_default_and_follows_the_tenant_default(
    hub: HubHarness,
) -> None:
    urgent = _urgency(hub, "urgent")
    background = _urgency(hub, "background")
    tenant_default = hub.client.get("/api/settings").json()["default_urgency"]

    created = _routine(hub, "pinned", default_urgency="urgent")
    assert created["effective_urgency"] == _ref(urgent)
    _routine(hub, "unset")
    assert _effective(hub) == {"pinned": _ref(urgent), "unset": tenant_default}

    moved = hub.client.patch("/api/settings", json={"default_urgency": "background"})
    assert moved.status_code == 200, moved.text
    assert _effective(hub) == {"pinned": _ref(urgent), "unset": _ref(background)}


def test_the_listing_reads_the_tenant_default_once_however_many_routines(tmp_path: Path) -> None:
    (tmp_path / "few").mkdir()
    (tmp_path / "many").mkdir()
    few = build_hub(tmp_path / "few")
    many = build_hub(tmp_path / "many")
    for harness, count in ((few, 1), (many, 5)):
        _urgency(harness, "urgent")
        for index in range(count):
            _routine(harness, f"r{index}", **({"default_urgency": "urgent"} if index % 2 else {}))

    def read(harness: HubHarness) -> None:
        assert harness.client.get("/api/routines").status_code == 200

    assert count_queries(few.engine, lambda: read(few)) == count_queries(many.engine, lambda: read(many))
