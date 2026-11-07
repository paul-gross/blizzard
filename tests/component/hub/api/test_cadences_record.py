"""Cadences as configured records over HTTP (component tier): each verb's status and body, the refusals naming the
field, ``If-Match`` conflicts, retire and enable that change nothing writing nothing, the list filters, the admission
a promoting write needs, and a list whose queries stay flat as its cadences grow — a real hub app driven through
``TestClient``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from blizzard.auth_core import Role
from blizzard.hub.api import auth_session
from blizzard.hub.auth.permissions import Permission, permissions_for
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.garden.scopes import ScopeSlug
from blizzard.wire.cadence import RevisionConflictProblem
from blizzard.wire.problem import REVISION_CONFLICT
from tests.support.refusal_observation import app_spec, declared_refusal_mismatches
from tests.support.scaffolding import HubHarness, build_hub, count_queries, default_project, project_record

_CTX = ChangeContext(actor="operator", door=Door.API)


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    return build_hub(tmp_path)


def _project(hub: HubHarness) -> str:
    return default_project(hub.engine)


def _routine(hub: HubHarness, slug: str, scope: str = "blizzard") -> None:
    graph = hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )
    hub.services.routine_authoring.create(
        project=project_record(hub, _project(hub)),
        ctx=_CTX,
        slug=slug,
        graph_name=graph.name,
        default_scope_slug=ScopeSlug.parse(scope),
    )


def _post(hub: HubHarness, body: dict[str, Any]) -> Any:
    return hub.client.post(f"/api/projects/{_project(hub)}/cadences", json=body)


def _create(hub: HubHarness, slug: str = "evening", **fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"slug": slug, "spacing": "10d", **fields}
    resp = _post(hub, body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _changes(hub: HubHarness) -> list[dict[str, Any]]:
    resp = hub.client.get("/api/config/changes", params={"record_kind": "cadence"})
    assert resp.status_code == 200, resp.text
    return list(reversed(resp.json()["changes"]))


def test_create_answers_201_with_the_view_its_chores_and_attention(hub: HubHarness) -> None:
    _routine(hub, "arch")

    body = _create(
        hub,
        name="Evening sweep",
        schedule={"cron": "0 20 * * *", "zone": "America/Chicago"},
        chores=[
            {"key": "arch", "routine": "arch", "scopes": "all", "mode": "delta"},
            {"key": "ghost", "routine": "nope"},
        ],
    )

    assert body["cadence_id"].startswith("cad_")
    assert (body["slug"], body["name"], body["revision"], body["retired"]) == ("evening", "Evening sweep", 1, False)
    assert body["schedule"] == {"cron": "0 20 * * *", "zone": "America/Chicago"}
    assert (body["spacing_minutes"], body["anchor"]) == (10 * 24 * 60, "started")
    assert [(c["key"], c["scopes"], c["mode"], c["promote"]) for c in body["chores"]] == [
        ("arch", "all", "delta", True),
        ("ghost", None, "full", True),
    ]
    assert all(c["chore_id"].startswith("chr_") for c in body["chores"])
    assert body["attention"] == [{"reason": "routine_unknown", "chore": "ghost", "scope": None}]
    assert body["project_id"] == _project(hub)
    [row] = _changes(hub)
    assert (row["record_key"], row["op"], row["project_id"]) == ("evening", "create", _project(hub))


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"spacing": None}, "schedule"),
        ({"schedule": {"cron": "0 0 * * * *", "zone": "UTC"}}, "schedule.cron"),
        ({"schedule": {"cron": "0 20 * * *", "zone": "Mars/Base"}}, "schedule.zone"),
        ({"spacing": "0m"}, "spacing"),
        ({"anchor": "whenever"}, "anchor"),
        ({"chores": [{"key": "a", "routine": "r"}, {"key": "a", "routine": "r"}]}, "chores[1].key"),
    ],
)
def test_a_create_that_cannot_mean_anything_is_422_naming_the_field(
    hub: HubHarness, fields: dict[str, Any], field: str
) -> None:
    resp = _post(hub, {"slug": "evening", "spacing": "1d", **fields})

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"][0]["loc"] == ["body", field]
    assert _changes(hub) == []


def test_a_slug_its_project_holds_is_422(hub: HubHarness) -> None:
    _create(hub)

    resp = _post(hub, {"slug": "evening", "spacing": "1d"})

    assert resp.status_code == 422, resp.text


def test_a_patch_edits_only_its_fields_keeps_a_kept_chores_id_and_409s_on_a_stale_if_match(hub: HubHarness) -> None:
    created = _create(hub, chores=[{"key": "arch", "routine": "arch"}])
    [chore] = created["chores"]
    url = f"/api/cadences/{created['cadence_id']}"

    edited = hub.client.patch(
        url, json={"chores": [{"key": "arch", "routine": "arch", "promote": False}]}, headers={"If-Match": "1"}
    )

    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert (body["revision"], body["spacing_minutes"]) == (2, 10 * 24 * 60)
    assert [(c["chore_id"], c["promote"]) for c in body["chores"]] == [(chore["chore_id"], False)]
    assert [d["field"] for d in _changes(hub)[-1]["diff"]] == ["chores.arch"]
    stale = hub.client.patch(url, json={"name": "late"}, headers={"If-Match": "1"})
    spec = app_spec(hub.client)
    assert (
        declared_refusal_mismatches(spec, stale, "patch", "/api/cadences/{cadence_id}", 409, RevisionConflictProblem)
        == []
    )
    assert stale.json()["type"] == REVISION_CONFLICT


def test_a_patch_clears_a_field_with_null_and_refuses_a_null_name(hub: HubHarness) -> None:
    created = _create(hub, schedule={"cron": "0 20 * * *", "zone": "UTC"})
    url = f"/api/cadences/{created['cadence_id']}"

    cleared = hub.client.patch(url, json={"spacing": None})
    assert cleared.status_code == 200, cleared.text
    assert (cleared.json()["spacing_minutes"], cleared.json()["anchor"]) == (None, None)

    refused = hub.client.patch(url, json={"name": None})
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"][0]["loc"] == ["body", "name"]
    emptied = hub.client.patch(url, json={"schedule": None})
    assert emptied.status_code == 422, emptied.text
    assert emptied.json()["detail"][0]["loc"] == ["body", "schedule"]


def test_an_unknown_cadence_is_404(hub: HubHarness) -> None:
    assert hub.client.get("/api/cadences/cad_nope").status_code == 404
    assert hub.client.patch("/api/cadences/cad_nope", json={"name": "x"}).status_code == 404
    assert hub.client.post("/api/cadences/cad_nope/retire", json={}).status_code == 404


def test_retire_and_enable_are_idempotent_and_write_nothing_when_redundant(hub: HubHarness) -> None:
    created = _create(hub)
    url = f"/api/cadences/{created['cadence_id']}"

    first = hub.client.post(f"{url}/retire", json={"by": "paul"})
    again = hub.client.post(f"{url}/retire", json={"by": "paul"})

    assert (first.status_code, again.status_code) == (202, 202)
    assert (first.json()["retired"], again.json()["revision"]) == (True, 2)
    assert first.json()["attention"] == []
    enabled = hub.client.post(f"{url}/enable", json={})
    enabled_again = hub.client.post(f"{url}/enable", json={})
    assert (enabled.json()["retired"], enabled_again.json()["revision"]) == (False, 3)
    assert [row["op"] for row in _changes(hub)] == ["create", "retire", "enable"]


def test_the_lists_filter_by_project_and_hide_a_retired_cadence_unless_asked(hub: HubHarness) -> None:
    kept = _create(hub, "kept")
    gone = _create(hub, "gone")
    assert hub.client.post(f"/api/cadences/{gone['cadence_id']}/retire", json={}).status_code == 202

    def slugs(url: str, **params: Any) -> list[str]:
        resp = hub.client.get(url, params=params)
        assert resp.status_code == 200, resp.text
        return sorted(c["slug"] for c in resp.json())

    project = _project(hub)
    assert slugs("/api/cadences") == ["kept"]
    assert slugs("/api/cadences", include_retired=True) == ["gone", "kept"]
    assert slugs("/api/cadences", project=project) == ["kept"]
    assert slugs(f"/api/projects/{project}/cadences", include_retired=True) == ["gone", "kept"]
    assert hub.client.get(f"/api/cadences/{kept['cadence_id']}").json()["slug"] == "kept"
    assert hub.client.get("/api/cadences", params={"project": "nowhere"}).status_code == 422
    assert hub.client.get("/api/projects/nowhere/cadences").status_code == 404


@pytest.fixture
def no_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    """Narrow every caller's permissions to all but ``chunk:control`` — no real role holds ``graph:edit`` without it."""

    def narrowed(role: Role | None, hub_admin: bool) -> frozenset[Permission]:
        return permissions_for(role, hub_admin) - {Permission.CHUNK_CONTROL}

    monkeypatch.setattr(auth_session, "permissions_for", narrowed)


@pytest.mark.usefixtures("no_admission")
def test_a_promoting_write_needs_chunk_control_and_retire_never_asks_it(hub: HubHarness) -> None:
    promoting = {"chores": [{"key": "arch", "routine": "arch"}]}
    assert _post(hub, {"slug": "evening", "spacing": "1d", **promoting}).status_code == 403
    held = _create(hub, chores=[{"key": "arch", "routine": "arch", "promote": False}])
    url = f"/api/cadences/{held['cadence_id']}"

    assert hub.client.patch(url, json=promoting).status_code == 403
    assert hub.client.post(f"{url}/retire", json={}).status_code == 202
    assert hub.client.patch(url, json=promoting).status_code == 200
    assert hub.client.post(f"{url}/enable", json={}).status_code == 403
    assert hub.client.post(f"{url}/retire", json={}).status_code == 202


def _seed(hub: HubHarness, count: int) -> None:
    for scope in ("blizzard", "other"):
        hub.services.scope_registry.ensure(project_record(hub, _project(hub)), ScopeSlug.parse(scope), _CTX)
    for index in range(count):
        _routine(hub, f"routine-{index}")
        _create(
            hub,
            f"cadence-{index}",
            chores=[
                {"key": "all", "routine": f"routine-{index}", "scopes": "all"},
                {"key": "listed", "routine": f"routine-{index}", "scopes": ["blizzard", "other", "missing"]},
                {"key": "ghost", "routine": "nope"},
            ],
        )


def test_the_list_reads_attention_in_a_flat_number_of_queries(tmp_path: Path) -> None:
    (tmp_path / "few").mkdir()
    (tmp_path / "many").mkdir()
    few, many = build_hub(tmp_path / "few"), build_hub(tmp_path / "many")
    _seed(few, 1)
    _seed(many, 5)

    def listed(hub: HubHarness) -> None:
        resp = hub.client.get("/api/cadences")
        assert resp.status_code == 200, resp.text
        assert all(c["attention"] for c in resp.json())

    assert count_queries(few.engine, lambda: listed(few)) == count_queries(many.engine, lambda: listed(many))
