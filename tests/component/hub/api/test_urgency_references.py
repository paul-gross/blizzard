"""A routine's default urgency and a chore's override over HTTP and in a configuration document (component tier, wired
hub app): each set by id or slug and read by slug, refused an unknown or a retired urgency, cleared by a retire with its
record's own revision and change row, declared beside the urgency it names in one document, and exported so that
applying the export back writes nothing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx2
import pytest

from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.garden.scopes import ScopeSlug
from tests.support.scaffolding import HubHarness, build_hub, default_project, project_record

_CTX = ChangeContext(actor="operator", door=Door.API)


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    return build_hub(tmp_path)


def _project(hub: HubHarness) -> str:
    return default_project(hub.engine)


def _graph(hub: HubHarness) -> str:
    return hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    ).name


def _urgency(hub: HubHarness, slug: str) -> dict[str, Any]:
    created = hub.client.post("/api/urgencies", json={"slug": slug})
    assert created.status_code == 201, created.text
    return created.json()


def _retire(hub: HubHarness, slug: str) -> None:
    retired = hub.client.post(f"/api/urgencies/{slug}/retire", json={})
    assert retired.status_code == 200, retired.text


def _routine(hub: HubHarness, slug: str = "sweep", **fields: Any) -> httpx2.Response:
    body = {"slug": slug, "graph_name": _graph(hub), "default_scope_slug": "blizzard", **fields}
    return hub.client.post(f"/api/projects/{_project(hub)}/routines", json=body)


def _cadence(hub: HubHarness, *chores: dict[str, Any]) -> httpx2.Response:
    body = {"slug": "evening", "spacing": "10d", "chores": list(chores)}
    return hub.client.post(f"/api/projects/{_project(hub)}/cadences", json=body)


def _refused(response: httpx2.Response) -> tuple[list[Any], str]:
    assert response.status_code == 422, response.text
    entry = response.json()["detail"][0]
    return entry["loc"], entry["type"]


def _changes(hub: HubHarness, kind: str) -> list[dict[str, Any]]:
    listed = hub.client.get("/api/config/changes", params={"record_kind": kind})
    assert listed.status_code == 200, listed.text
    return list(reversed(listed.json()["changes"]))


def _apply(hub: HubHarness, document: dict[str, object]) -> httpx2.Response:
    return hub.client.post("/api/config/apply", json={"version": 2, **document})


def test_a_routine_takes_its_default_by_id_or_slug_reads_it_by_slug_and_clears_it_with_null(hub: HubHarness) -> None:
    urgent = _urgency(hub, "urgent")
    _urgency(hub, "background")

    created = _routine(hub, default_urgency=urgent["urgency_id"])
    assert created.status_code == 201, created.text
    routine = created.json()
    assert routine["default_urgency"] == "urgent"

    moved = hub.client.patch(f"/api/routines/{routine['routine_id']}", json={"default_urgency": "background"})
    assert (moved.status_code, moved.json()["default_urgency"], moved.json()["revision"]) == (200, "background", 2)
    cleared = hub.client.patch(f"/api/routines/{routine['routine_id']}", json={"default_urgency": None})
    assert (cleared.json()["default_urgency"], cleared.json()["revision"]) == (None, 3)
    diffs = [[(d["field"], d["old"], d["new"]) for d in c["diff"]] for c in _changes(hub, "routine")[1:]]
    assert diffs == [[("default_urgency", "urgent", "background")], [("default_urgency", "background", None)]]


def test_a_routine_or_chore_naming_an_unknown_or_retired_urgency_is_refused_naming_the_field(hub: HubHarness) -> None:
    _urgency(hub, "old")
    _retire(hub, "old")

    assert _refused(_routine(hub, default_urgency="nosuch")) == (["body", "default_urgency"], "urgency_unknown")
    assert _refused(_routine(hub, default_urgency="old")) == (["body", "default_urgency"], "urgency_retired")
    routine = _routine(hub).json()
    edit = hub.client.patch(f"/api/routines/{routine['routine_id']}", json={"default_urgency": "old"})
    assert _refused(edit) == (["body", "default_urgency"], "urgency_retired")

    chore = {"key": "nightly", "routine": "sweep"}
    assert _refused(_cadence(hub, {**chore, "urgency": "nosuch"})) == (
        ["body", "chores[0].urgency"],
        "urgency_unknown",
    )
    assert _refused(_cadence(hub, {**chore, "urgency": "old"})) == (["body", "chores[0].urgency"], "urgency_retired")
    assert hub.client.get(f"/api/routines/{routine['routine_id']}").json()["revision"] == 1


def test_an_urgency_only_chore_edit_keeps_the_chore_and_leaves_the_other_chores_override(hub: HubHarness) -> None:
    urgent = _urgency(hub, "urgent")
    _routine(hub)
    created = _cadence(hub, {"key": "nightly", "routine": "sweep", "urgency": urgent["urgency_id"]})
    assert created.status_code == 201, created.text
    cadence = created.json()
    (chore,) = cadence["chores"]
    assert chore["urgency"] == "urgent"

    chores = [
        {"key": "nightly", "routine": "sweep", "urgency": "urgent"},
        {"key": "weekly", "routine": "sweep"},
    ]
    edited = hub.client.patch(f"/api/cadences/{cadence['cadence_id']}", json={"chores": chores})
    assert edited.status_code == 200, edited.text
    kept, added = edited.json()["chores"]
    assert (kept["chore_id"], kept["urgency"], added["urgency"]) == (chore["chore_id"], "urgent", None)


def test_a_retire_leaves_a_routines_default_and_a_chores_override_reading_none(hub: HubHarness) -> None:
    _urgency(hub, "urgent")
    routine = _routine(hub, default_urgency="urgent").json()
    _routine(hub, "other")
    cadence = _cadence(
        hub,
        {"key": "nightly", "routine": "sweep", "urgency": "urgent"},
        {"key": "weekly", "routine": "other"},
    ).json()

    _retire(hub, "urgent")

    after = hub.client.get(f"/api/routines/{routine['routine_id']}").json()
    assert (after["default_urgency"], after["revision"]) == (None, 2)
    kept = hub.client.get(f"/api/cadences/{cadence['cadence_id']}").json()
    assert ([c["urgency"] for c in kept["chores"]], kept["revision"]) == ([None, None], 2)
    assert [c["chore_id"] for c in kept["chores"]] == [c["chore_id"] for c in cadence["chores"]]
    (routine_change,) = [c for c in _changes(hub, "routine") if c["record_key"] == "sweep" and c["revision"] == 2]
    assert [(d["field"], d["old"], d["new"]) for d in routine_change["diff"]] == [("default_urgency", "urgent", None)]
    (cadence_change,) = [c for c in _changes(hub, "cadence") if c["revision"] == 2]
    assert [d["field"] for d in cadence_change["diff"]] == ["chores.nightly"]
    assert cadence_change["diff"][0]["new"]["urgency"] is None


def test_one_document_declares_an_urgency_and_the_routine_and_chore_naming_it(hub: HubHarness) -> None:
    applied = _apply(
        hub,
        {
            "urgencies": [{"slug": "background"}],
            "scopes": [{"slug": "blizzard"}],
            "routines": [
                {
                    "slug": "sweep",
                    "graph_name": _graph(hub),
                    "default_scope_slug": "blizzard",
                    "default_urgency": "background",
                }
            ],
            "cadences": [
                {
                    "slug": "evening",
                    "spacing": "10d",
                    "chores": [{"key": "nightly", "routine": "sweep", "urgency": "background"}],
                }
            ],
        },
    )

    assert applied.status_code == 200, applied.text
    (routine,) = hub.client.get("/api/routines").json()
    assert routine["default_urgency"] == "background"
    (cadence,) = hub.client.get("/api/cadences").json()
    assert cadence["chores"][0]["urgency"] == "background"


@pytest.mark.parametrize(
    ("section", "entry", "field"),
    [
        (
            "routines",
            {"slug": "sweep", "default_scope_slug": "blizzard", "default_urgency": "{key}"},
            "default_urgency",
        ),
        (
            "cadences",
            {"slug": "evening", "spacing": "10d", "chores": [{"key": "a", "routine": "x", "urgency": "{key}"}]},
            "chores[0].urgency",
        ),
    ],
)
@pytest.mark.parametrize(("key", "refusal"), [("old", "urgency_retired"), ("nosuch", "urgency_unknown")])
def test_a_document_naming_a_retired_or_unknown_urgency_is_refused_at_the_entry(
    hub: HubHarness, section: str, entry: dict[str, Any], field: str, key: str, refusal: str
) -> None:
    _urgency(hub, "old")
    _retire(hub, "old")
    if section == "routines":
        entry = {**entry, "graph_name": _graph(hub), "default_urgency": key}
    else:
        entry = {**entry, "chores": [{**entry["chores"][0], "urgency": key}]}

    refused = _apply(hub, {"scopes": [{"slug": "blizzard"}], section: [entry]})

    assert _refused(refused) == (["body", section, 0, field], refusal)
    assert hub.client.get(f"/api/{section}").json() == []


def test_an_export_applied_back_writes_nothing(hub: HubHarness) -> None:
    _urgency(hub, "urgent")
    hub.services.routine_authoring.create(
        project=project_record(hub, _project(hub)),
        ctx=_CTX,
        slug="sweep",
        graph_name=_graph(hub),
        default_scope_slug=ScopeSlug.parse("blizzard"),
        default_urgency=hub.services.urgency_reading.named("default_urgency", "urgent"),
    )
    _routine(hub, "other")
    assert _cadence(hub, {"key": "nightly", "routine": "sweep", "urgency": "urgent"}).status_code == 201

    exported = hub.client.get("/api/config/export")
    assert exported.status_code == 200, exported.text
    applied = hub.client.post("/api/config/apply", json=exported.json())

    assert applied.status_code == 200, applied.text
    assert {o["op"] for o in applied.json()["outcomes"]} == {"unchanged"}
