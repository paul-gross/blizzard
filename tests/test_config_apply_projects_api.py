"""Projects in a configuration document — ``POST /api/config/apply`` and ``GET /api/config/export`` with a
``projects`` section and project-named scope and routine entries (component tier, wired hub app)."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.projects.model import ProjectEdit, ProjectSlug
from tests.support import HubHarness, build_hub

pytestmark = pytest.mark.component

_SOURCE = {"slug": "demo", "provider": "github", "locator": "acme/demo", "secret": "gh", "annotate": True}
_OTHER_SOURCE = {**_SOURCE, "slug": "infra", "locator": "acme/infra"}
_REPO = {
    "slug": "blizzard",
    "forge_api_url": "https://api.github.com",
    "owner": "acme",
    "repo": "blizzard",
    "base_branch": "master",
    "secret_name": "gh",
}
_GRAPH = """
name: alpha
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
_WEB = {"slug": "web", "name": "Web", "sources": ["demo"], "repositories": ["blizzard"]}


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path, repositories=(), link=False)
    assert hub.client.post("/api/secrets", json={"name": "gh", "value": "v"}).status_code == 201
    return hub


def _apply(hub: HubHarness, document: dict[str, object], *, dry_run: bool = False):
    return hub.client.post("/api/config/apply", params={"dry_run": dry_run}, json={"version": 1, **document})


def _rows(applied) -> list[tuple[str, str, str]]:
    assert applied.status_code == 200, applied.text
    return [(o["kind"], o["key"], o["op"]) for o in applied.json()["outcomes"]]


def _changes(hub: HubHarness) -> list[dict[str, object]]:
    return hub.client.get("/api/config/changes", params={"limit": 200}).json()["changes"]


def _project_id(hub: HubHarness, slug: str) -> str:
    project = hub.services.projects.get_by_slug(ProjectSlug(slug))
    assert project is not None
    return project.project_id


def _repository_slug(hub: HubHarness, repository_id: str) -> str:
    return next(
        r.slug
        for r in hub.services.repository_records.list_all(include_retired=True)
        if r.repository_id == repository_id
    )


def _source_slug(hub: HubHarness, work_source_id: str) -> str:
    return next(
        r.slug
        for r in hub.services.work_source_records.list_all(include_retired=True)
        if r.work_source_id == work_source_id
    )


def _linked(hub: HubHarness, slug: str) -> tuple[list[str], list[str]]:
    project_id = _project_id(hub, slug)
    links = hub.services.project_links
    return (
        sorted(_source_slug(hub, link.work_source_id) for link in links.sources_of(project_id)),  # type: ignore[arg-type]
        sorted(_repository_slug(hub, link.repository_id) for link in links.repositories_of(project_id)),  # type: ignore[arg-type]
    )


def test_a_projects_entry_creates_the_project_and_its_links_with_the_apply_id(hub: HubHarness) -> None:
    applied = _apply(hub, {"work_sources": [_SOURCE], "repositories": [_REPO], "projects": [_WEB]})
    assert _rows(applied) == [
        ("forge", "github", "create"),
        ("work_source", "demo", "create"),
        ("repository", "blizzard", "create"),
        ("project", "web", "create"),
        ("project_source_link", "web/demo", "create"),
        ("project_repository_link", "web/blizzard", "create"),
    ]
    assert _linked(hub, "web") == (["demo"], ["blizzard"])
    apply_id = applied.json()["apply_id"]
    written = [c for c in _changes(hub) if c["apply_id"] == apply_id]
    assert {(c["record_kind"], c["door"]) for c in written} == {
        ("forge", "apply"),
        ("work_source", "apply"),
        ("repository", "apply"),
        ("project", "apply"),
        ("project_source_link", "apply"),
        ("project_repository_link", "apply"),
    }


def test_a_declared_repository_is_linked_to_no_project_until_an_entry_links_it(hub: HubHarness) -> None:
    _rows(_apply(hub, {"repositories": [_REPO]}))
    assert _linked(hub, "default") == ([], [])


def test_a_scope_may_name_a_project_the_document_declares_by_slug_or_one_stored_by_id(hub: HubHarness) -> None:
    default_id = _project_id(hub, "default")
    applied = _apply(
        hub,
        {
            "projects": [{"slug": "web", "name": "Web"}],
            "scopes": [{"slug": "core", "project": "web"}, {"slug": "core", "project": default_id}],
        },
    )
    assert _rows(applied) == [
        ("project", "web", "create"),
        ("scope", "core", "create"),
        ("scope", "core", "create"),
    ]
    homes = sorted(scope.project_id for scope in hub.services.scopes.list_all())
    assert homes == sorted([default_id, _project_id(hub, "web")])


def test_an_entry_naming_no_project_lands_in_the_only_one_and_is_refused_with_several(hub: HubHarness) -> None:
    assert _rows(_apply(hub, {"scopes": [{"slug": "core"}]})) == [("scope", "core", "create")]
    (scope,) = hub.services.scopes.list_all()
    assert scope.project_id == _project_id(hub, "default")

    before = _changes(hub)
    refused = _apply(hub, {"projects": [{"slug": "web", "name": "Web"}], "scopes": [{"slug": "edge"}]})
    assert refused.status_code == 422
    (detail,) = refused.json()["detail"]
    assert detail["loc"] == ["body", "scopes", 0, "project"]
    assert detail["type"] == "project_required"
    assert [p["slug"] for p in detail["projects"]] == ["default", "web"]
    assert _changes(hub) == before
    assert hub.services.projects.get_by_slug(ProjectSlug("web")) is None


def test_a_project_unknown_to_the_store_and_the_document_is_refused_at_the_entry(hub: HubHarness) -> None:
    refused = _apply(
        hub, {"routines": [{"name": "n", "graph_name": "alpha", "default_scope_slug": "c", "project": "x"}]}
    )
    assert refused.status_code == 422
    assert refused.json()["detail"][0]["loc"] == ["body", "routines", 0, "project"]


def test_a_stated_link_list_is_the_whole_set_and_an_omitted_one_is_left(hub: HubHarness) -> None:
    _rows(_apply(hub, {"work_sources": [_SOURCE, _OTHER_SOURCE], "repositories": [_REPO], "projects": [_WEB]}))

    moved = _apply(hub, {"projects": [{"slug": "web", "sources": ["infra"]}]})
    assert _rows(moved) == [
        ("project_source_link", "web/infra", "create"),
        ("project_source_link", "web/demo", "retire"),
    ]
    assert _linked(hub, "web") == (["infra"], ["blizzard"])

    back = _apply(hub, {"projects": [{"slug": "web", "sources": ["demo", "infra"]}]})
    assert _rows(back) == [("project_source_link", "web/demo", "enable")]
    assert _rows(_apply(hub, {"projects": [{"slug": "web"}]})) == [("project", "web", "unchanged")]
    assert _linked(hub, "web") == (["demo", "infra"], ["blizzard"])


@pytest.mark.parametrize(
    ("entry", "field"),
    [
        ({"slug": "web", "name": "Web", "sources": ["hub"]}, "sources"),
        ({"slug": "web", "name": "Web", "sources": ["nope"]}, "sources"),
        ({"slug": "web", "name": "Web", "repositories": ["nope"]}, "repositories"),
        ({"slug": "web"}, "name"),
        ({"slug": "Not A Slug", "name": "x"}, "slug"),
    ],
)
def test_a_projects_entry_that_cannot_apply_is_refused_at_its_field(
    hub: HubHarness, entry: dict[str, object], field: str
) -> None:
    before = _changes(hub)
    refused = _apply(hub, {"projects": [entry]})
    assert refused.status_code == 422
    assert refused.json()["detail"][0]["loc"] == ["body", "projects", 0, field]
    assert _changes(hub) == before


def test_a_former_slug_is_refused_naming_the_project_that_left_it(hub: HubHarness) -> None:
    _rows(_apply(hub, {"projects": [{"slug": "web", "name": "Web"}]}))
    web = hub.services.projects.get_by_slug(ProjectSlug("web"))
    assert web is not None
    hub.services.project_authoring.edit(web, ProjectEdit(slug="site"), ChangeContext(actor="op", door=Door.API))
    before = _changes(hub)

    refused = _apply(hub, {"projects": [{"slug": "web", "name": "Web again"}]})

    assert refused.status_code == 422
    (detail,) = refused.json()["detail"]
    assert detail["loc"] == ["body", "projects", 0, "slug"]
    assert "'site'" in detail["msg"]
    assert _changes(hub) == before


def test_a_dry_run_plans_projects_and_links_without_writing_them(hub: HubHarness) -> None:
    document = {"work_sources": [_SOURCE], "repositories": [_REPO], "projects": [_WEB], "scopes": [{"slug": "c"}]}
    refused = _apply(hub, document, dry_run=True)
    assert refused.status_code == 422 and refused.json()["detail"][0]["type"] == "project_required"
    document["scopes"] = [{"slug": "c", "project": "web"}]
    dry = _apply(hub, document, dry_run=True)
    assert hub.services.projects.get_by_slug(ProjectSlug("web")) is None
    assert _rows(dry) == _rows(_apply(hub, document))


def test_an_export_of_a_two_project_tenant_applies_back_as_a_byte_stable_no_op(hub: HubHarness) -> None:
    assert hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH}).status_code == 201
    routine = {"name": "nightly", "graph_name": "alpha", "default_scope_slug": "core", "scopes": ["core"]}
    _rows(
        _apply(
            hub,
            {
                "work_sources": [_SOURCE],
                "repositories": [_REPO],
                "projects": [_WEB],
                "scopes": [{"slug": "core", "project": "web"}, {"slug": "core", "project": "default"}],
                "routines": [{**routine, "project": "web"}, {**routine, "project": "default"}],
            },
        )
    )
    exported = hub.client.get("/api/config/export")
    document = exported.json()
    assert [p["slug"] for p in document["projects"]] == ["default", "web"]
    assert [(s["project"], s["slug"]) for s in document["scopes"]] == [("default", "core"), ("web", "core")]
    assert [(r["project"], r["name"]) for r in document["routines"]] == [("default", "nightly"), ("web", "nightly")]

    before = _changes(hub)
    assert {op for _, _, op in _rows(_apply(hub, document))} == {"unchanged"}
    assert _changes(hub) == before
    assert hub.client.get("/api/config/export").content == exported.content


def test_a_projects_sources_name_a_source_by_id_slug_or_former_slug(hub: HubHarness) -> None:
    _rows(_apply(hub, {"work_sources": [_SOURCE, _OTHER_SOURCE], "projects": [{"slug": "web", "name": "Web"}]}))
    infra_id = hub.client.get("/api/work-sources/infra").json()["work_source_id"]
    renamed = hub.client.patch("/api/work-sources/demo", json={"slug": "demo-core"})
    assert renamed.status_code == 200, renamed.text

    linked = _apply(hub, {"projects": [{"slug": "web", "sources": ["demo", infra_id]}]})
    assert _rows(linked) == [
        ("project_source_link", "web/demo-core", "create"),
        ("project_source_link", "web/infra", "create"),
    ]
    assert _linked(hub, "web") == (["demo-core", "infra"], [])
    assert hub.client.get("/api/config/export").json()["projects"][1]["sources"] == ["demo-core", "infra"]


def test_a_work_source_entry_keyed_by_a_slug_a_source_left_is_refused_naming_it(hub: HubHarness) -> None:
    _rows(_apply(hub, {"work_sources": [_SOURCE]}))
    source_id = hub.client.get("/api/work-sources/demo").json()["work_source_id"]
    assert hub.client.patch("/api/work-sources/demo", json={"slug": "demo-core"}).status_code == 200

    refused = _apply(hub, {"work_sources": [{**_SOURCE, "locator": "acme/fresh"}]})
    assert refused.status_code == 422, refused.text
    detail = refused.json()["detail"][0]
    assert detail["loc"] == ["body", "work_sources", 0, "slug"]
    assert "'demo-core'" in detail["msg"] and source_id in detail["msg"]

    kept = _apply(hub, {"work_sources": [{**_SOURCE, "slug": "demo-core", "name": "Demo core"}]})
    assert _rows(kept) == [("work_source", "demo-core", "edit")]
    assert hub.client.get("/api/work-sources/demo").json()["name"] == "Demo core"


def test_a_former_slug_another_source_reclaims_resolves_to_its_new_holder(hub: HubHarness) -> None:
    _rows(_apply(hub, {"work_sources": [_SOURCE]}))
    first = hub.client.get("/api/work-sources/demo").json()["work_source_id"]
    assert hub.client.patch("/api/work-sources/demo", json={"slug": "demo-old"}).status_code == 200
    assert hub.client.get("/api/work-sources/demo").json()["work_source_id"] == first

    reclaimed = hub.client.post("/api/work-sources", json={**_SOURCE, "locator": "acme/fresh"})
    assert reclaimed.status_code == 201, reclaimed.text
    second = reclaimed.json()["work_source_id"]
    assert second != first
    assert hub.client.get("/api/work-sources/demo").json()["work_source_id"] == second
    assert hub.client.get("/api/work-sources/demo-old").json()["work_source_id"] == first
    assert hub.client.get(f"/api/work-sources/{first}").json()["slug"] == "demo-old"


def test_an_id_never_resolves_as_a_slug_and_hub_is_never_a_slug(hub: HubHarness) -> None:
    _rows(_apply(hub, {"work_sources": [_SOURCE]}))
    source_id = hub.client.get("/api/work-sources/demo").json()["work_source_id"]
    unknown_id = "wsrc_01JZZZZZZZZZZZZZZZZZZZZZZZ"
    assert hub.client.get(f"/api/work-sources/{unknown_id}").status_code == 404
    assert hub.client.post("/api/work-sources", json={**_SOURCE, "slug": source_id}).status_code == 422
    assert hub.client.patch("/api/work-sources/demo", json={"slug": "hub"}).status_code == 422
    assert hub.client.post("/api/work-sources", json={**_SOURCE, "slug": "hub"}).status_code == 422
    refused = _apply(hub, {"work_sources": [{**_SOURCE, "slug": "hub", "locator": "acme/hub"}]})
    assert refused.status_code == 422 and refused.json()["detail"][0]["loc"][-1] == "slug"


def test_a_document_entry_states_a_color_or_takes_the_least_used_and_an_off_palette_one_is_refused(
    hub: HubHarness,
) -> None:
    applied = _apply(
        hub,
        {
            "projects": [
                {"slug": "one", "name": "One"},
                {"slug": "two", "name": "Two"},
                {"slug": "p", "name": "P", "color": "pink"},
            ]
        },
    )
    _rows(applied)

    colors = {p["slug"]: p["color"] for p in hub.client.get("/api/projects").json()["projects"]}
    assert colors["p"] == "pink"
    assert len({colors["default"], colors["one"], colors["two"]}) == 3

    refused = _apply(hub, {"projects": [{"slug": "bad", "name": "Bad", "color": "mauve"}]})
    assert refused.status_code == 422, refused.text
    assert "color" in refused.text


def test_a_document_entry_states_a_hex_color_stored_lowercase_and_a_malformed_one_is_refused(hub: HubHarness) -> None:
    _rows(_apply(hub, {"projects": [{"slug": "p", "name": "P", "color": "#FF8800"}]}))
    colors = {p["slug"]: p["color"] for p in hub.client.get("/api/projects").json()["projects"]}
    assert colors["p"] == "#ff8800"

    refused = _apply(hub, {"projects": [{"slug": "bad", "name": "Bad", "color": "#abc"}]})
    assert refused.status_code == 422, refused.text
    assert "#rrggbb" in refused.text


def test_a_stated_color_edits_a_stored_project_and_an_export_carries_it(hub: HubHarness) -> None:
    _rows(_apply(hub, {"projects": [{"slug": "web", "name": "Web", "color": "blue"}]}))
    _rows(_apply(hub, {"projects": [{"slug": "web", "color": "violet"}]}))

    exported = hub.client.get("/api/config/export").json()
    assert {p["slug"]: p["color"] for p in exported["projects"]}["web"] == "violet"


def test_an_entry_stating_a_null_color_is_refused_whether_the_project_exists_or_not(hub: HubHarness) -> None:
    _rows(_apply(hub, {"projects": [{"slug": "web", "name": "Web", "color": "blue"}]}))

    stored = _apply(hub, {"projects": [{"slug": "web", "color": None}]})
    created = _apply(hub, {"projects": [{"slug": "new", "name": "New", "color": None}]})

    for refused in (stored, created):
        assert refused.status_code == 422, refused.text
        assert "color" in refused.text
    assert {p["slug"]: p["color"] for p in hub.client.get("/api/projects").json()["projects"]}["web"] == "blue"
    assert "new" not in {p["slug"] for p in hub.client.get("/api/projects").json()["projects"]}
