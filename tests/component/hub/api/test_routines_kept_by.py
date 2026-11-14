"""``RoutineView.kept_by`` over HTTP (component tier): the chores keeping a routine on every route that serves a
routine view, and a list whose queries stay flat as its routines grow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.garden.scopes import ScopeSlug
from tests.support.scaffolding import HubHarness, build_hub, count_queries, default_project, project_record

_CTX = ChangeContext(actor="operator", door=Door.API)


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    return build_hub(tmp_path)


def _graph(hub: HubHarness) -> str:
    return hub.services.default_graph.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    ).name


def _routine(hub: HubHarness, slug: str) -> str:
    return hub.services.routine_authoring.create(
        project=project_record(hub, default_project(hub.engine)),
        ctx=_CTX,
        slug=slug,
        graph_name=_graph(hub),
        default_scope_slug=ScopeSlug.parse("blizzard"),
    ).routine_id


def _cadence(hub: HubHarness, slug: str, chores: list[dict[str, Any]], **fields: Any) -> str:
    body = {"slug": slug, "chores": chores, **({"spacing": "1d"} if "schedule" not in fields else {}), **fields}
    resp = hub.client.post(f"/api/projects/{default_project(hub.engine)}/cadences", json=body)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["cadence_id"])


def _rows(view: dict[str, Any]) -> list[tuple[str, str, bool]]:
    return sorted((k["cadence_slug"], k["chore_key"], k["next_moment"] is not None) for k in view["kept_by"])


def _keeping(hub: HubHarness) -> str:
    routine_id = _routine(hub, "arch")
    _cadence(
        hub,
        "evening",
        [{"key": "a", "routine": "arch"}, {"key": "b", "routine": "arch"}],
        schedule={"cron": "0 20 * * *", "zone": "UTC"},
    )
    _cadence(hub, "spaced", [{"key": "c", "routine": "arch"}, {"key": "x", "routine": "other"}])
    return routine_id


_EXPECTED = [("evening", "a", True), ("evening", "b", True), ("spaced", "c", False)]


def test_a_routine_created_after_its_cadences_carries_the_rows_on_both_creates(hub: HubHarness) -> None:
    _routine(hub, "seed")
    _cadence(hub, "keeper", [{"key": "a", "routine": "fresh"}])
    project = default_project(hub.engine)
    body = {"slug": "fresh", "graph_name": _graph(hub), "default_scope_slug": "blizzard"}

    nested = hub.client.post(f"/api/projects/{project}/routines", json=body)
    legacy = hub.client.post("/api/routines", json={**body, "slug": "fresh2"})

    assert nested.status_code == 201, nested.text
    assert _rows(nested.json()) == [("keeper", "a", False)]
    assert legacy.status_code == 201, legacy.text
    assert legacy.json()["kept_by"] == []


def test_get_lists_patch_retire_and_enable_carry_the_rows(hub: HubHarness) -> None:
    routine_id = _keeping(hub)
    project = default_project(hub.engine)
    url = f"/api/routines/{routine_id}"

    assert _rows(hub.client.get(url).json()) == _EXPECTED
    listed = hub.client.get(f"/api/projects/{project}/routines").json()
    assert _rows(next(r for r in listed if r["routine_id"] == routine_id)) == _EXPECTED
    tenant = hub.client.get("/api/routines").json()
    assert _rows(next(r for r in tenant if r["routine_id"] == routine_id)) == _EXPECTED
    assert _rows(hub.client.patch(url, json={"name": "Renamed"}).json()) == _EXPECTED
    assert _rows(hub.client.post(f"{url}/retire", json={}).json()) == _EXPECTED
    assert _rows(hub.client.post(f"{url}/enable", json={}).json()) == _EXPECTED


def test_a_scheduled_row_carries_the_cadences_first_next_moment(hub: HubHarness) -> None:
    routine_id = _keeping(hub)
    evening = next(c for c in hub.client.get("/api/cadences").json() if c["slug"] == "evening")

    rows = hub.client.get(f"/api/routines/{routine_id}").json()["kept_by"]
    row = next(r for r in rows if r["cadence_slug"] == "evening")

    assert row["next_moment"] == evening["next_moments"][0]


def test_a_retired_cadence_stops_keeping_and_enabling_it_restores_the_row(hub: HubHarness) -> None:
    routine_id = _routine(hub, "arch")
    cadence_id = _cadence(hub, "gone", [{"key": "a", "routine": "arch"}])
    url = f"/api/routines/{routine_id}"

    assert hub.client.post(f"/api/cadences/{cadence_id}/retire", json={}).status_code == 202
    assert hub.client.get(url).json()["kept_by"] == []
    assert hub.client.post(f"/api/cadences/{cadence_id}/enable", json={}).status_code == 202
    assert _rows(hub.client.get(url).json()) == [("gone", "a", False)]


def test_the_lists_read_cadences_in_a_flat_number_of_queries(tmp_path: Path) -> None:
    (tmp_path / "few").mkdir()
    (tmp_path / "many").mkdir()
    few, many = build_hub(tmp_path / "few"), build_hub(tmp_path / "many")
    for h, n in ((few, 1), (many, 5)):
        for i in range(n):
            _routine(h, f"r{i}")
        _cadence(h, "all", [{"key": f"k{i}", "routine": f"r{i}"} for i in range(n)])

    def listed(h: HubHarness, path: str) -> None:
        assert h.client.get(path).status_code == 200

    def paths(h: HubHarness) -> tuple[str, str]:
        return "/api/routines", f"/api/projects/{default_project(h.engine)}/routines"

    for h in (few, many):  # a first read warms lazily-filled caches the comparison must not count
        for path in paths(h):
            listed(h, path)
    for few_path, many_path in zip(paths(few), paths(many), strict=True):
        assert count_queries(few.engine, lambda p=few_path: listed(few, p)) == count_queries(
            many.engine, lambda p=many_path: listed(many, p)
        )
