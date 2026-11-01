"""Gardening under ``/api/projects/{project}`` (component tier): every nested verb acts in the project its path
names, by id, slug or a slug the project left; an unknown project answers ``404``. The tenant-wide listings narrow
to the project ``?project=`` names, a run of another project answers ``404`` under a project, the fleet's scope
vocabulary narrows to a chunk's project, and every garden view names its project."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.foundation.run_mode import RunMode
from blizzard.hub.domain.chunk.model import WorkItemAuthor
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.garden.scopes import ScopeSlug
from tests.support import HubHarness, build_hub, project_record

pytestmark = pytest.mark.component

_CTX = ChangeContext(actor="operator", door=Door.API)
#: A month around the harness clock: runs and proposals minted at its instant fall inside it.
_WINDOW = {"since": "2026-07-01T00:00:00Z", "until": "2026-08-01T00:00:00Z"}


def _ok(response, status: int = 200):
    assert response.status_code == status, response.text
    return response.json()


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path)
    _ok(hub.client.post("/api/projects", json={"slug": "widgets", "name": "Widgets"}), 201)
    return hub


def _graph_name(hub: HubHarness) -> str:
    return hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    ).name


def _routine(hub: HubHarness, project: str, name: str = "nightly") -> dict[str, object]:
    body = {"name": name, "graph_name": _graph_name(hub), "default_scope_slug": "blizzard"}
    return _ok(hub.client.post(f"/api/projects/{project}/routines", json=body), 201)


def _run(hub: HubHarness, project: str) -> str:
    """A routine run minted in ``project``, its chunk promoted."""
    resolved = hub.services.projects.resolve(project)
    assert resolved is not None
    routine = hub.services.routine_authoring.create(
        ctx=_CTX,
        name=f"{project}-sweep",
        graph_name=_graph_name(hub),
        default_scope_slug=ScopeSlug.parse("blizzard"),
        default_model=None,
        default_effort=None,
        project=project_record(hub, resolved.project_id),
    )
    scope = hub.services.scopes.get(routine.project_id, routine.default_scope_slug)
    assert scope is not None
    result = hub.services.routine_run.run(
        routine, scope=scope, mode=RunMode.FULL, note=None, author=WorkItemAuthor.user("usr_1")
    )
    hub.promote(result.chunk_id)
    return result.chunk_id


def _proposal(hub: HubHarness, project: str, title: str = "t") -> dict[str, object]:
    body = {"class": "c", "title": title, "body": "b", "findings": []}
    return _ok(hub.client.post(f"/api/projects/{project}/garden-proposals", json=body), 201)


def test_every_nested_scope_verb_acts_in_the_project_its_path_names(hub: HubHarness) -> None:
    created = _ok(hub.client.post("/api/projects/widgets/scopes", json={"slug": "blizzard"}), 201)
    assert (created["project_slug"], created["project_name"]) == ("widgets", "Widgets")
    base = "/api/projects/widgets/scopes/blizzard"

    assert [s["slug"] for s in _ok(hub.client.get("/api/projects/widgets/scopes"))] == ["blizzard"]
    assert _ok(hub.client.get(base))["project_slug"] == "widgets"
    assert _ok(hub.client.patch(base, json={"description": "d"}))["description"] == "d"
    assert _ok(hub.client.post(f"{base}/retire", json={}), 202)["retired"] is True
    assert _ok(hub.client.post(f"{base}/enable", json={}), 202)["retired"] is False
    routine = _routine(hub, "widgets")
    assert _ok(hub.client.get(f"{base}/routines")) == [routine["routine_id"]]
    assert _ok(hub.client.get("/api/projects/default/scopes")) == []
    assert hub.client.get("/api/projects/default/scopes/blizzard").status_code == 404


def test_the_same_slug_in_two_projects_mints_two_scopes(hub: HubHarness) -> None:
    widgets = _ok(hub.client.post("/api/projects/widgets/scopes", json={"slug": "blizzard"}), 201)
    default = _ok(hub.client.post("/api/projects/default/scopes", json={"slug": "blizzard"}), 201)

    assert (widgets["project_slug"], default["project_slug"]) == ("widgets", "default")
    every = _ok(hub.client.get("/api/scopes"))
    assert sorted((s["slug"], s["project_slug"]) for s in every) == [("blizzard", "default"), ("blizzard", "widgets")]


def test_nested_routines_findings_and_proposals_act_in_their_project(hub: HubHarness) -> None:
    routine = _routine(hub, "widgets")
    assert (routine["project_slug"], routine["project_name"]) == ("widgets", "Widgets")
    proposal = _proposal(hub, "widgets")
    assert proposal["project_slug"] == "widgets"

    assert [r["name"] for r in _ok(hub.client.get("/api/projects/widgets/routines"))] == ["nightly"]
    assert _ok(hub.client.get("/api/projects/default/routines")) == []
    listed = _ok(hub.client.get("/api/projects/widgets/garden-proposals"))["proposals"]
    assert [p["proposal_id"] for p in listed] == [proposal["proposal_id"]]
    assert _ok(hub.client.get("/api/projects/default/garden-proposals"))["proposals"] == []
    assert _ok(hub.client.get("/api/projects/widgets/findings")) == {"findings": [], "next_cursor": None}


def test_an_unknown_project_answers_404_and_a_slug_the_project_left_is_served(hub: HubHarness) -> None:
    _ok(hub.client.post("/api/projects/widgets/scopes", json={"slug": "blizzard"}), 201)
    _ok(hub.client.patch("/api/projects/widgets", json={"slug": "gadgets"}))

    for path in ("scopes", "routines", "findings", "garden-proposals", "runs"):
        assert hub.client.get(f"/api/projects/nope/{path}").status_code == 404, path
    assert [s["slug"] for s in _ok(hub.client.get("/api/projects/widgets/scopes"))] == ["blizzard"]
    assert _ok(hub.client.get("/api/projects/widgets/scopes/blizzard"))["project_slug"] == "gadgets"


def test_the_tenant_wide_listings_narrow_to_the_project_named(hub: HubHarness) -> None:
    _ok(hub.client.post("/api/projects/widgets/scopes", json={"slug": "w-scope"}), 201)
    _ok(hub.client.post("/api/projects/default/scopes", json={"slug": "d-scope"}), 201)
    _routine(hub, "widgets", "w-routine")
    _routine(hub, "default", "d-routine")
    _proposal(hub, "widgets", "w-proposal")
    _proposal(hub, "default", "d-proposal")

    def slugs(path: str, key: str, project: str | None = None) -> set[str]:
        params = {"project": project} if project is not None else {}
        body = _ok(hub.client.get(path, params=params))
        rows = body["proposals"] if isinstance(body, dict) else body
        return {row[key] for row in rows}

    assert slugs("/api/scopes", "slug", "widgets") == {"w-scope", "blizzard"}
    assert slugs("/api/scopes", "slug") == {"w-scope", "d-scope", "blizzard"}
    assert slugs("/api/routines", "name", "widgets") == {"w-routine"}
    assert slugs("/api/routines", "name", "default") == {"d-routine"}
    assert slugs("/api/garden-proposals", "title", "widgets") == {"w-proposal"}
    assert slugs("/api/garden-proposals", "title") == {"w-proposal", "d-proposal"}
    assert hub.client.get("/api/scopes", params={"project": "nope"}).status_code == 422


def test_a_run_answers_under_its_own_project_only(hub: HubHarness) -> None:
    widgets_run = _run(hub, "widgets")
    default_run = _run(hub, "default")

    assert _ok(hub.client.get(f"/api/projects/widgets/runs/{widgets_run}"))["project_slug"] == "widgets"
    assert hub.client.get(f"/api/projects/default/runs/{widgets_run}").status_code == 404
    assert _ok(hub.client.get(f"/api/runs/{widgets_run}"))["project_name"] == "Widgets"
    assert [r["chunk_id"] for r in _ok(hub.client.get("/api/projects/widgets/runs", params=_WINDOW))] == [widgets_run]
    assert [r["chunk_id"] for r in _ok(hub.client.get("/api/runs", params={**_WINDOW, "project": "default"}))] == [
        default_run
    ]
    every = {r["chunk_id"]: r["project_slug"] for r in _ok(hub.client.get("/api/runs", params=_WINDOW))}
    assert every == {widgets_run: "widgets", default_run: "default"}


def test_the_fleet_scope_vocabulary_narrows_to_a_chunks_project(hub: HubHarness) -> None:
    widgets_run = _run(hub, "widgets")
    _ok(hub.client.post("/api/projects/default/scopes", json={"slug": "d-scope"}), 201)

    narrowed = _ok(hub.client.get("/api/fleet/scopes", params={"chunk_id": widgets_run}))
    assert [s["slug"] for s in narrowed] == ["blizzard"]
    assert {s["slug"] for s in _ok(hub.client.get("/api/fleet/scopes"))} == {"blizzard", "d-scope"}
    assert hub.client.get("/api/fleet/scopes", params={"chunk_id": "chk_nope"}).status_code == 404


def test_trend_and_proposal_counts_narrow_to_the_project_named(hub: HubHarness) -> None:
    _routine(hub, "widgets")
    _routine(hub, "default")
    _proposal(hub, "widgets")
    _proposal(hub, "default")
    trend = {"routine": "nightly", "introduced_boundary": "2026-07-20T00:00:00Z", **_WINDOW}

    assert (
        _ok(hub.client.get("/api/routines/trend", params={**trend, "project": "widgets"}))["routine_name"] == "nightly"
    )
    assert hub.client.get("/api/routines/trend", params=trend).json()["detail"]["code"] == "project_required"
    rows = _ok(hub.client.get("/api/routines/proposal-counts", params={**_WINDOW, "project": "widgets"}))["rows"]
    assert [(r["project_slug"], r["created"]) for r in rows] == [("widgets", 1)]
    every = _ok(hub.client.get("/api/routines/proposal-counts", params=_WINDOW))["rows"]
    assert sorted(r["project_slug"] for r in every) == ["default", "widgets"]
