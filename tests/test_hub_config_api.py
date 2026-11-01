"""The work-source record verbs, the served schema, and the change-log read (component tier) —
a real hub app driven through ``TestClient``."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict, Unpack

import httpx2
import pytest
from sqlalchemy import Connection

from blizzard.auth_core import Role
from blizzard.hub.api.user_names import user_names
from blizzard.hub.domain.config import secret_lifecycle
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.store.internal import repository_record_store, work_source_record_store
from blizzard.hub.store.internal.former_slugs import SlugTables
from blizzard.hub.store.internal.secret_store import SecretStore, record_lifecycle_fact
from tests.support import HubHarness, build_hub, hub_store_connections, seed_session, seed_user

pytestmark = pytest.mark.component

_SENTINEL = "tok-planted-840"


class _WorkSourceBody(TypedDict, total=False):
    slug: str
    name: str
    provider: str
    locator: str
    secret: str | None
    annotate: bool
    api_base: str
    web_base: str


class _RepositoryBody(TypedDict, total=False):
    slug: str
    name: str
    forge_api_url: str
    owner: str
    repo: str
    base_branch: str
    secret_name: str | None


_DEMO: _WorkSourceBody = {
    "slug": "demo",
    "provider": "github",
    "locator": "acme/demo",
    "secret": "gh",
    "annotate": True,
}


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path, repositories=(), link=False)
    assert hub.client.post("/api/secrets", json={"name": "gh", "value": _SENTINEL}).status_code == 201
    return hub


def _create(hub: HubHarness, **overrides: Unpack[_WorkSourceBody]) -> httpx2.Response:
    return hub.client.post("/api/work-sources", json={**_DEMO, **overrides})


class _FieldChange(TypedDict):
    field: str
    old: object
    new: object
    old_label: str | None
    new_label: str | None


class _ChangeRow(TypedDict):
    """One served row of the change log."""

    id: int
    recorded_at: str
    actor: str
    actor_name: str | None
    door: str
    record_kind: str
    record_key: str
    record_label: str | None
    revision: int
    op: str
    diff: list[_FieldChange]
    apply_id: str | None
    project_id: str | None


class _ChangePage(TypedDict):
    changes: list[_ChangeRow]
    next_before: int | None
    total: int


def _changes(hub: HubHarness, *, record_kind: str | None = None, record_key: str | None = None) -> list[_ChangeRow]:
    query = {
        key: value for key, value in (("record_kind", record_kind), ("record_key", record_key)) if value is not None
    }
    resp = hub.client.get("/api/config/changes", params=query)
    assert resp.status_code == 200, resp.text
    page: _ChangePage = resp.json()
    return page["changes"]


def test_create_show_list_and_the_built_in_hub(hub: HubHarness) -> None:
    created = _create(hub)
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["slug"], body["name"], body["revision"], body["retired"], body["built_in"]) == (
        "demo",
        "demo",
        1,
        False,
        False,
    )
    assert body["work_source_id"].startswith("wsrc_")
    assert (body["provider"], body["locator"], body["secret"], body["annotate"]) == (
        "github",
        "acme/demo",
        "gh",
        True,
    )
    assert body["created_by"] == "operator"
    # The create named its forge the old way and is warned; a read answers no warning.
    assert [w["field"] for w in body.pop("warnings")] == ["api_base"]
    body["warnings"] = []
    assert hub.client.get("/api/work-sources/demo").json() == body
    assert hub.client.get(f"/api/work-sources/{body['work_source_id']}").json() == body

    listed = hub.client.get("/api/work-sources").json()["sources"]
    by_name = {s["slug"]: s for s in listed}
    assert by_name["hub"]["built_in"] is True
    assert by_name["hub"]["provider"] is None
    assert by_name["hub"]["edit"] is True
    assert by_name["demo"]["edit"] is False
    assert by_name["demo"]["annotate"] is True
    assert {"work_source_id", "slug", "name", "annotate", "edit"} <= set(by_name["demo"])
    assert (by_name["hub"]["work_source_id"], by_name["hub"]["name"]) == (None, "hub")
    assert hub.client.get("/api/work-sources/hub").json()["built_in"] is True
    assert hub.client.get("/api/work-sources/missing").status_code == 404


def test_the_create_422s_name_the_offending_field(hub: HubHarness) -> None:
    cases: tuple[tuple[_WorkSourceBody, str], ...] = (
        ({"slug": "a:b"}, "slug"),
        ({"slug": "Demo"}, "slug"),
        ({"slug": "hub"}, "slug"),
        ({"name": " padded"}, "name"),
        ({"provider": "svn"}, "provider"),
        ({"locator": "nope"}, "locator"),
        ({"secret": "absent"}, "secret"),
        ({"secret": None}, "secret"),
    )
    for overrides, field in cases:
        resp = hub.client.post("/api/work-sources", json={**_DEMO, **overrides})
        assert resp.status_code == 422, (overrides, resp.text)
        assert resp.json()["detail"][0]["loc"][-1] == field, overrides
    assert hub.client.post("/api/work-sources", json={**_DEMO, "bogus": 1}).status_code == 422
    assert _changes(hub, record_kind="work_source") == []


def test_a_retired_secret_is_a_422_on_create_and_on_enable(hub: HubHarness) -> None:
    assert _create(hub).status_code == 201
    hub.client.post("/api/work-sources/demo/retire")
    assert hub.client.post("/api/secrets/gh/retire").status_code == 200
    assert _create(hub, slug="other", locator="acme/other").status_code == 422
    enabled = hub.client.post("/api/work-sources/demo/enable")
    assert enabled.status_code == 422
    assert enabled.json()["detail"][0]["loc"][-1] == "secret"
    assert hub.client.patch("/api/work-sources/demo", json={"annotate": False}).status_code == 422


def test_a_taken_name_or_locator_is_a_409_naming_the_holder(hub: HubHarness) -> None:
    assert _create(hub).status_code == 201
    assert _create(hub).status_code == 409
    clash = _create(hub, slug="demo2")
    assert clash.status_code == 409
    assert "demo" in clash.json()["detail"]
    hub.client.post("/api/work-sources/demo/retire")
    held = _create(hub, slug="demo2")
    assert held.status_code == 409
    assert "work source demo" in held.json()["detail"]


def _bypass_in_write_checks(monkeypatch: pytest.MonkeyPatch, module: object, holder_reader: str) -> None:
    """Skip the store's in-write slug and holder checks, so a collision reaches the unique constraint the way a
    concurrent writer that committed first would; the holder read after the lost race still runs for real."""
    monkeypatch.undo()
    real: Callable[[Connection, object], str | None] = getattr(module, holder_reader)
    calls: list[object] = []

    def first_blind(conn: Connection, record: object) -> str | None:
        calls.append(record)
        return None if len(calls) == 1 else real(conn, record)

    def refuse_nothing(self: SlugTables, conn: Connection, record_id: str, slug: str) -> None:
        return None

    monkeypatch.setattr(SlugTables, "refuse_held", refuse_nothing)
    monkeypatch.setattr(module, holder_reader, first_blind)


def test_a_lost_unique_race_on_create_names_the_constraint_that_fired(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _create(hub).status_code == 201
    _bypass_in_write_checks(monkeypatch, work_source_record_store, "_locator_holder")
    locator = _create(hub, slug="demo2")
    assert locator.status_code == 409, locator.text
    assert locator.json()["detail"] == "github locator acme/demo already belongs to work source demo; enable it instead"

    _bypass_in_write_checks(monkeypatch, work_source_record_store, "_locator_holder")
    slug = _create(hub, locator="acme/other")
    assert slug.status_code == 409, slug.text
    assert slug.json()["detail"] == "work source demo already exists"


def test_a_lost_unique_race_on_update_names_the_constraint_that_fired(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _create(hub).status_code == 201
    assert _create(hub, slug="other", locator="acme/other").status_code == 201
    _bypass_in_write_checks(monkeypatch, work_source_record_store, "_locator_holder")
    locator = hub.client.patch("/api/work-sources/other", json={"locator": "acme/demo"})
    assert locator.status_code == 409, locator.text
    assert locator.json()["detail"] == "github locator acme/demo already belongs to work source demo; enable it instead"

    _bypass_in_write_checks(monkeypatch, work_source_record_store, "_locator_holder")
    slug = hub.client.patch("/api/work-sources/other", json={"slug": "demo"})
    assert slug.status_code == 409, slug.text
    assert slug.json()["detail"] == "work source demo already exists"


def test_patch_is_sparse_and_tells_absent_from_null(hub: HubHarness) -> None:
    _create(hub, api_base="https://ghe.example/api/v3", web_base="https://ghe.example")

    untouched = hub.client.patch("/api/work-sources/demo", json={})
    assert untouched.status_code == 200
    assert untouched.json()["revision"] == 1
    same = hub.client.patch("/api/work-sources/demo", json={"annotate": True, "secret": "gh"})
    assert same.json()["revision"] == 1

    edited = hub.client.patch("/api/work-sources/demo", json={"annotate": False}).json()
    assert (edited["revision"], edited["annotate"], edited["api_base"]) == (2, False, "https://ghe.example/api/v3")
    kept = hub.client.patch("/api/work-sources/demo", json={"web_base": None}).json()
    assert (kept["revision"], kept["web_base"], kept["api_base"]) == (
        2,
        "https://ghe.example",
        "https://ghe.example/api/v3",
    )

    for body, field in (
        ({"secret": None}, "secret"),
        ({"annotate": None}, "annotate"),
        ({"provider": None}, "provider"),
    ):
        resp = hub.client.patch("/api/work-sources/demo", json=body)
        assert resp.status_code == 422, body
        assert resp.json()["detail"][0]["loc"][-1] == field
    for body, field in (({"slug": None}, "slug"), ({"slug": "hub"}, "slug"), ({"name": None}, "name")):
        resp = hub.client.patch("/api/work-sources/demo", json=body)
        assert resp.status_code == 422, body
        assert resp.json()["detail"][0]["loc"][-1] == field
    assert hub.client.patch("/api/work-sources/nope", json={}).status_code == 404
    assert hub.client.get("/api/work-sources/demo").json()["revision"] == 2


def test_if_match_is_checked_on_patch_retire_and_enable(hub: HubHarness) -> None:
    _create(hub)
    hub.client.patch("/api/work-sources/demo", json={"annotate": False})

    def patch(h: dict[str, str]) -> httpx2.Response:
        return hub.client.patch("/api/work-sources/demo", json={"annotate": True}, headers=h)

    def retire(h: dict[str, str]) -> httpx2.Response:
        return hub.client.post("/api/work-sources/demo/retire", headers=h)

    def enable(h: dict[str, str]) -> httpx2.Response:
        return hub.client.post("/api/work-sources/demo/enable", headers=h)

    for call in (patch, retire, enable):
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
    assert "demo" not in {s["slug"] for s in hub.client.get("/api/work-sources").json()["sources"]}
    listed = hub.client.get("/api/work-sources", params={"include_retired": True}).json()["sources"]
    assert "demo" in {s["slug"] for s in listed}
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
    source_id = _create(hub).json()["work_source_id"]
    shown = hub.client.get("/api/secrets/gh").json()
    assert shown["references"] == [{"kind": "work_source", "key": source_id, "label": "demo"}]
    assert hub.client.get("/api/secrets").json()[0]["references"] == shown["references"]
    refused = hub.client.post("/api/secrets/gh/retire")
    assert refused.status_code == 409
    assert "work_source demo" in refused.json()["detail"]["message"]
    assert hub.client.get("/api/secrets/gh").json()["retired"] is False
    hub.client.post("/api/work-sources/demo/retire")
    assert hub.client.get("/api/secrets/gh").json()["references"] == []
    assert hub.client.post("/api/secrets/gh/retire").status_code == 200


def test_a_secret_retire_held_open_blocks_an_enable_which_then_refuses(hub: HubHarness) -> None:
    assert _create(hub).status_code == 201
    assert hub.client.post("/api/work-sources/demo/retire").status_code == 200
    connections = hub_store_connections(hub.engine)
    metadata = SecretStore(connections).get("gh")
    assert metadata is not None
    change = secret_lifecycle.lifecycle_change(
        metadata, ChangeContext(actor="op", door=Door.API), retired_now=False, retired=True, at=hub.clock.now()
    )
    assert change is not None
    retire_open, release = threading.Event(), threading.Event()
    enabled: list[int] = []

    def retire() -> None:
        with connections.write("retire") as conn:
            record_lifecycle_fact(conn, "gh", retired=True, at=hub.clock.now(), by="op", change=change)
            retire_open.set()
            release.wait(timeout=5)

    def enable() -> None:
        enabled.append(hub.client.post("/api/work-sources/demo/enable").status_code)

    retiring = threading.Thread(target=retire)
    retiring.start()
    assert retire_open.wait(timeout=5)
    enabling = threading.Thread(target=enable)
    enabling.start()
    enabling.join(timeout=0.5)
    assert enabled == []  # the enable waits on the open retire
    release.set()
    retiring.join(timeout=5)
    enabling.join(timeout=5)

    assert enabled == [422]
    assert hub.client.get("/api/secrets/gh").json()["retired"] is True
    assert hub.client.get("/api/work-sources/demo").json()["retired"] is True


def test_the_door_is_recorded_per_header_value(hub: HubHarness) -> None:
    for i, door in enumerate(["cli", "board", "apply", "migration", "nonsense", None]):
        headers = {"X-Blizzard-Door": door} if door else {}
        resp = hub.client.post(
            "/api/work-sources", json={**_DEMO, "slug": f"s{i}", "locator": f"acme/s{i}"}, headers=headers
        )
        assert resp.status_code == 201
    doors = {c["record_label"]: c["door"] for c in _changes(hub, record_kind="work_source")}
    assert doors == {"s0": "cli", "s1": "board", "s2": "api", "s3": "api", "s4": "api", "s5": "api"}
    secret_rows = _changes(hub, record_kind="secret")
    assert [(c["op"], c["door"], c["actor"]) for c in secret_rows] == [("create", "api", "operator")]


def test_the_change_log_pages_newest_first_and_filters(hub: HubHarness) -> None:
    source_id = _create(hub).json()["work_source_id"]
    hub.client.patch("/api/work-sources/demo", json={"annotate": False})
    full = hub.client.get("/api/config/changes").json()
    # The oldest row is the store's default project's, which the migration wrote.
    assert [(c["record_kind"], c["op"]) for c in full["changes"]] == [
        ("work_source", "edit"),
        ("work_source", "create"),
        ("forge", "create"),
        ("secret", "create"),
        ("project", "create"),
    ]
    assert full["next_before"] is None
    edit = full["changes"][0]
    assert edit["diff"] == [{"field": "annotate", "old": True, "new": False, "old_label": None, "new_label": None}]
    assert (edit["record_kind"], edit["record_key"], edit["record_label"], edit["revision"], edit["actor"]) == (
        "work_source",
        source_id,
        "demo",
        2,
        "operator",
    )
    assert len(_changes(hub, record_kind="work_source", record_key="demo")) == 2

    first = hub.client.get("/api/config/changes", params={"limit": 2}).json()
    assert len(first["changes"]) == 2
    assert first["next_before"] == first["changes"][-1]["id"]
    rest = hub.client.get("/api/config/changes", params={"limit": 2, "before": first["next_before"]}).json()
    assert [(c["record_kind"], c["op"]) for c in rest["changes"]] == [("forge", "create"), ("secret", "create")]
    assert rest["next_before"] == rest["changes"][-1]["id"]
    assert len(_changes(hub, record_key="gh")) == 1
    assert hub.client.get("/api/config/changes", params={"limit": 201}).status_code == 422
    assert hub.client.get("/api/config/changes", params={"record_kind": "bogus"}).status_code == 422


def test_the_schema_endpoint_describes_the_document_model(hub: HubHarness) -> None:
    schema = hub.client.get("/api/config/schema/work-sources")
    assert schema.status_code == 200
    body = schema.json()
    assert set(_DEMO) <= set(body["properties"])
    assert body["required"] == ["slug", "provider", "locator"]
    assert body["additionalProperties"] is False
    repositories = hub.client.get("/api/config/schema/repositories")
    assert repositories.status_code == 200
    assert repositories.json()["required"] == [
        "slug",
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
    hub = build_hub(tmp_path, auth_mode="oauth", repositories=(), link=False)
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

_REPO: _RepositoryBody = {
    "slug": "blizzard",
    "forge_api_url": "https://api.github.com",
    "owner": "acme",
    "repo": "blizzard",
    "base_branch": "master",
    "secret_name": "gh",
}


def _create_repo(hub: HubHarness, **overrides: Unpack[_RepositoryBody]) -> httpx2.Response:
    return hub.client.post("/api/repositories", json={**_REPO, **overrides})


def test_a_repository_creates_shows_and_lists(hub: HubHarness) -> None:
    created = _create_repo(hub)
    assert created.status_code == 201, created.text
    body = created.json()
    assert {k: body[k] for k in _REPO} == _REPO
    assert (body["revision"], body["retired"], body["created_by"]) == (1, False, "operator")
    assert body["repository_id"].startswith("repo_")
    assert body["name"] == "blizzard"
    # The create named its forge the old way and is warned; a read answers no warning.
    assert [w["field"] for w in body.pop("warnings")] == ["forge_api_url"]
    body["warnings"] = []
    assert hub.client.get("/api/repositories/blizzard").json() == body
    assert hub.client.get(f"/api/repositories/{body['repository_id']}").json() == body
    assert hub.client.get("/api/repositories").json()["repositories"] == [body]
    assert hub.client.get("/api/repositories/missing").status_code == 404


def test_the_repository_create_422s_name_the_offending_field(hub: HubHarness) -> None:
    cases: tuple[tuple[_RepositoryBody, str], ...] = (
        ({"name": " "}, "name"),
        ({"slug": "Not A Slug"}, "slug"),
        ({"owner": ""}, "owner"),
        ({"forge_api_url": "ftp://forge"}, "forge_api_url"),
        ({"forge_api_url": "api.github.com"}, "forge_api_url"),
        ({"secret_name": "absent"}, "secret_name"),
        ({"secret_name": None}, "secret_name"),
    )
    for overrides, field in cases:
        resp = hub.client.post("/api/repositories", json={**_REPO, **overrides})
        assert resp.status_code == 422, (overrides, resp.text)
        assert resp.json()["detail"][0]["loc"][-1] == field, overrides
    assert hub.client.post("/api/repositories", json={**_REPO, "bogus": 1}).status_code == 422
    assert _changes(hub, record_kind="repository") == []


def test_a_taken_repository_slug_or_coordinate_is_a_409(hub: HubHarness) -> None:
    assert _create_repo(hub).status_code == 201
    assert _create_repo(hub).status_code == 409
    hub.client.post("/api/repositories/blizzard/retire")
    held = _create_repo(hub, slug="other")
    assert held.status_code == 409
    assert "repository blizzard" in held.json()["detail"]
    assert _create_repo(hub, slug="other", forge_api_url="https://ghe.example/api/v3").status_code == 201


def test_a_lost_unique_race_on_a_repository_write_names_the_constraint_that_fired(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _create_repo(hub).status_code == 201
    assert _create_repo(hub, slug="other", repo="other").status_code == 201
    coordinate_detail = (
        "github (https://api.github.com) acme/blizzard already belongs to repository blizzard; enable it instead"
    )
    for write in (
        lambda: _create_repo(hub, slug="third"),
        lambda: hub.client.patch("/api/repositories/other", json={"repo": "blizzard"}),
    ):
        _bypass_in_write_checks(monkeypatch, repository_record_store, "_coordinate_holder")
        coordinate = write()
        assert coordinate.status_code == 409, coordinate.text
        assert coordinate.json()["detail"] == coordinate_detail
    for write in (
        lambda: _create_repo(hub, repo="third"),
        lambda: hub.client.patch("/api/repositories/other", json={"slug": "blizzard"}),
    ):
        _bypass_in_write_checks(monkeypatch, repository_record_store, "_coordinate_holder")
        slug = write()
        assert slug.status_code == 409, slug.text
        assert slug.json()["detail"] == "repository blizzard already exists"


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
    assert hub.client.patch("/api/repositories/blizzard", json={"slug": "Not A Slug"}).status_code == 422
    assert hub.client.patch("/api/repositories/blizzard", json={"slug": None}).status_code == 422
    assert hub.client.patch("/api/repositories/blizzard", json={"name": None}).status_code == 422
    assert hub.client.patch("/api/repositories/blizzard", json={"secret_name": "absent"}).status_code == 422
    assert hub.client.patch("/api/repositories/nope", json={}).status_code == 404

    def patch(h: dict[str, str]) -> httpx2.Response:
        return hub.client.patch("/api/repositories/blizzard", json={"base_branch": "dev"}, headers=h)

    def retire(h: dict[str, str]) -> httpx2.Response:
        return hub.client.post("/api/repositories/blizzard/retire", headers=h)

    def enable(h: dict[str, str]) -> httpx2.Response:
        return hub.client.post("/api/repositories/blizzard/enable", headers=h)

    for call in (patch, retire, enable):
        stale = call({"If-Match": "1"})
        assert stale.status_code == 409
        assert "revision 2" in stale.json()["detail"]


def test_repository_retire_and_enable_move_the_revision_and_guard_the_secret(hub: HubHarness) -> None:
    _create_repo(hub)
    repository_id = hub.client.get("/api/repositories/blizzard").json()["repository_id"]
    assert hub.client.get("/api/secrets/gh").json()["references"] == [
        {"kind": "repository", "key": repository_id, "label": "blizzard"}
    ]
    refused = hub.client.post("/api/secrets/gh/retire")
    assert refused.status_code == 409
    assert "repository blizzard" in refused.json()["detail"]["message"]

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
    hub = build_hub(tmp_path, auth_mode="oauth", repositories=(), link=False)
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
    "slug": "blizzard",
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
  - {slug: demo, provider: github, locator: acme/demo, secret: gh, annotate: true}
repositories:
  - {slug: blizzard, forge_api_url: "https://api.github.com", owner: acme, repo: blizzard, base_branch: master, secret_name: gh}
"""


def _apply_json(
    hub: HubHarness, document: dict[str, Any], *, dry_run: bool = False, headers: dict[str, Any] | None = None
):
    return hub.client.post("/api/config/apply", params={"dry_run": dry_run}, json=document, headers=headers or {})


def _apply_yaml(hub: HubHarness, text: str, *, dry_run: bool = False):
    return hub.client.post(
        "/api/config/apply", params={"dry_run": dry_run}, content=text.encode(), headers={"Content-Type": _YAML}
    )


def _forge_ids_masked(outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``outcomes`` with each minted forge id masked: two applies each mint their own forge for one origin."""
    return [
        {
            **outcome,
            "diff": [
                {**change, "new": "frg_*"} if change["field"] == "forge" and change["old"] is None else change
                for change in outcome["diff"]
            ],
        }
        for outcome in outcomes
    ]


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
    assert _forge_ids_masked(from_json.json()["outcomes"]) == _forge_ids_masked(from_yaml.json()["outcomes"])
    assert [(o["kind"], o["key"], o["op"]) for o in from_json.json()["outcomes"]] == [
        ("forge", "github", "create"),
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
    assert _forge_ids_masked(real.json()["outcomes"]) == _forge_ids_masked(dry.json()["outcomes"])


def test_the_rows_an_apply_writes_carry_the_apply_door_and_one_apply_id(hub: HubHarness) -> None:
    applied = _apply_yaml(hub, _YAML_DOCUMENT).json()
    rows = [r for r in _changes(hub) if r["door"] == "apply"]
    assert [r["record_kind"] for r in rows] == ["repository", "work_source", "forge"]
    assert {r["apply_id"] for r in rows} == {applied["apply_id"]}
    assert _apply_yaml(hub, _YAML_DOCUMENT).json()["outcomes"] == [
        {"kind": "work_source", "key": "demo", "op": "unchanged", "diff": []},
        {"kind": "repository", "key": "blizzard", "op": "unchanged", "diff": []},
    ]
    assert len([r for r in _changes(hub) if r["door"] == "apply"]) == 3


def test_a_claimed_apply_door_on_another_route_still_records_api(hub: HubHarness) -> None:
    created = hub.client.post("/api/work-sources", json=_DEMO, headers={"X-Blizzard-Door": "apply"})
    assert created.status_code == 201
    assert _changes(hub, record_kind="work_source", record_key="demo")[0]["door"] == "api"
    applied = _apply_json(
        hub,
        {"version": 1, "work_sources": [{**_DEMO, "annotate": False}]},
        headers={"X-Blizzard-Door": "cli"},
    )
    assert applied.status_code == 200
    assert _changes(hub, record_kind="work_source", record_key="demo")[0]["door"] == "apply"


def test_an_omitted_field_is_left_and_a_stated_one_is_restored(hub: HubHarness) -> None:
    _apply_yaml(hub, _YAML_DOCUMENT)
    hub.client.patch("/api/work-sources/demo", json={"annotate": False})
    partial = {"version": 1, "work_sources": [{"slug": "demo", "provider": "github", "locator": "acme/demo"}]}
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

    invalid = _apply_json(hub, {"version": 1, "work_sources": [_DEMO, {**_DEMO, "slug": "b", "provider": "x"}]})
    assert invalid.json()["detail"][0]["loc"] == ["body", "work_sources", 1, "provider"]

    shape = _apply_json(hub, {"version": 3})
    assert shape.status_code == 422
    assert shape.json()["detail"][0]["loc"] == ["body", "version"]

    twice = _apply_json(hub, {"version": 1, "work_sources": [_DEMO, _DEMO]})
    assert twice.status_code == 422 and twice.json()["detail"][0]["loc"][:3] == ["body", "work_sources", 1]

    clash = _apply_json(hub, {"version": 1, "work_sources": [_DEMO, {**_DEMO, "slug": "other"}]})
    assert clash.status_code == 409 and "work_sources[1]" in clash.json()["detail"]

    built_in = _apply_json(hub, {"version": 1, "work_sources": [{**_DEMO, "slug": "hub"}]})
    assert built_in.status_code == 422
    assert built_in.json()["detail"][0]["loc"] == ["body", "work_sources", 0, "slug"]

    unslugged = _apply_json(hub, {"version": 1, "work_sources": [{k: v for k, v in _DEMO.items() if k != "slug"}]})
    assert unslugged.status_code == 422
    assert unslugged.json()["detail"][0]["loc"] == ["body", "work_sources", 0, "slug"]
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
    hub.client.post("/api/work-sources", json={**_DEMO, "slug": "gone", "locator": "acme/gone"})
    hub.client.post("/api/work-sources/gone/retire")
    exported = hub.client.get("/api/config/export")
    assert exported.status_code == 200
    document = exported.json()
    assert document["version"] == 1
    assert list(document)[:2] == ["version", "projects"]
    assert document["projects"] == [
        {"slug": "default", "name": "Default", "description": "", "color": "red", "sources": [], "repositories": []}
    ]
    assert document["secrets"] == ["gh"]
    assert [w["slug"] for w in document["work_sources"]] == ["demo"]
    assert [r["slug"] for r in document["repositories"]] == ["blizzard"]
    assert set(document["work_sources"][0]) == {
        "slug",
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
    assert [o["op"] for o in replay.json()["outcomes"]] == ["unchanged", "unchanged", "unchanged"]
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


def _rows(applied: httpx2.Response) -> list[tuple[str, str, str]]:
    assert applied.status_code == 200, applied.text
    return [(o["kind"], o["key"], o["op"]) for o in applied.json()["outcomes"]]


def _routine(hub: HubHarness, name: str = "nightly") -> dict[str, Any]:
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
    assert _forge_ids_masked(real.json()["outcomes"]) == _forge_ids_masked(dry.json()["outcomes"])
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
    assert relinked == [
        {"field": "scopes", "old": ["core", "edge"], "new": ["edge"], "old_label": None, "new_label": None}
    ]
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
    assert document["scopes"] == [
        {"slug": "core", "description": "the core", "project": "default"},
        {"slug": "edge", "description": "", "project": "default"},
    ]
    assert document["routines"] == [
        {
            "name": "nightly",
            "graph_name": "alpha",
            "default_scope_slug": "core",
            "default_model": [],
            "default_effort": None,
            "default_harnesses": [],
            "scopes": ["core", "edge"],
            "project": "default",
        }
    ]
    before = _changes(hub)
    assert {op for _, _, op in _rows(_apply_json(hub, document))} == {"unchanged"}
    assert _changes(hub) == before


def test_the_change_log_names_user_actors_total_and_lenses_a_project(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth", repositories=(), link=False)
    ada = seed_user(hub, username="ada", role=Role.ADMIN, display_name="Ada Lovelace")
    ada_headers = {"Authorization": f"Bearer {seed_session(hub, ada)}"}
    guest = {"Authorization": f"Bearer {seed_session(hub, seed_user(hub, username='gus', role=Role.GUEST))}"}
    for slug in ("alpha", "beta"):
        created = hub.client.post("/api/projects", json={"slug": slug, "name": slug}, headers=ada_headers)
        assert created.status_code == 201, created.text
    assert hub.client.post("/api/secrets", json={"name": "gh", "value": "v"}, headers=ada_headers).status_code == 201
    alpha = hub.client.get("/api/projects/alpha", headers=guest).json()["project_id"]

    def read(*, limit: int | None = None, before: int | None = None, project: str | None = None) -> _ChangePage:
        pairs = (("limit", limit), ("before", before), ("project", project))
        query = {key: value for key, value in pairs if value is not None}
        resp = hub.client.get("/api/config/changes", params=query, headers=guest)
        assert resp.status_code == 200, resp.text
        return resp.json()

    everything = read()
    one = read(limit=1)
    assert len(one["changes"]) == 1 and one["total"] == everything["total"] == len(everything["changes"]) > 2
    assert read(limit=1, before=one["changes"][0]["id"])["total"] == everything["total"]

    named = {c["actor"]: c["actor_name"] for c in everything["changes"]}
    assert named[ada.user_id] == "Ada Lovelace"
    assert named["migration"] is None

    lensed = read(project="alpha")
    assert {c["project_id"] for c in lensed["changes"]} == {alpha, None}
    assert lensed["total"] == len(lensed["changes"]) < everything["total"]


def test_admin_records_name_a_user_actor_and_leave_a_non_user_actor_to_its_id(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth", repositories=(), link=False)
    ada = seed_user(hub, username="ada", role=Role.ADMIN, display_name="Ada Lovelace")
    headers = {"Authorization": f"Bearer {seed_session(hub, ada)}"}
    assert hub.client.post("/api/secrets", json={"name": "gh", "value": "v"}, headers=headers).status_code == 201
    repository = {"slug": "r", "forge_api_url": "https://api.github.com", "owner": "o", "repo": "r"}
    assert (
        hub.client.post(
            "/api/repositories", json={**repository, "base_branch": "master", "secret_name": "gh"}, headers=headers
        ).status_code
        == 201
    )
    assert hub.client.post("/api/work-sources", json={**_DEMO, "secret": "gh"}, headers=headers).status_code == 201

    secret = hub.client.get("/api/secrets/gh", headers=headers).json()
    listed_repository = hub.client.get("/api/repositories", headers=headers).json()["repositories"][0]
    source = hub.client.get("/api/work-sources/demo", headers=headers).json()
    assert secret["replaced_by"] == ada.user_id and secret["replaced_by_name"] == "Ada Lovelace"
    assert listed_repository["created_by_name"] == "Ada Lovelace"
    assert source["created_by"] == ada.user_id and source["created_by_name"] == "Ada Lovelace"

    # A stored id that is no user — a config door, a deleted user — carries no name.
    assert user_names(hub.services, [ada.user_id, "migration", "usr_gone"]) == {ada.user_id: "Ada Lovelace"}
    assert hub.client.get("/api/work-sources/hub", headers=headers).json()["created_by_name"] is None
