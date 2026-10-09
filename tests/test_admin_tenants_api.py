"""The tenant administration routes over the hub app with sign-in on: only a hub administrator creates, lists, renames
and deletes tenants and syncs the packaged graphs into every open tenant; a name without a tenant name's shape answers
``422``; a tenant's own admin is refused all of it; a rename reaches a runner's next registration; the tenant-scoped graph sync stays, deprecated, under ``GRAPH_EDIT``."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.auth_core import Role
from blizzard.hub.api.auth_session import TENANT_HEADER
from blizzard.hub.config import AUTH_MODE_OAUTH
from blizzard.hub.domain.tenancy import teardown
from blizzard.hub.graphs import PACKAGED, GraphFile
from blizzard.hub.tenancy import TenantId
from tests.support import HubHarness, build_hub, default_tenant, seed_hub_admin, seed_runner, seed_session, seed_user

pytestmark = pytest.mark.component

_PACKAGED_NAMES = sorted(GraphFile(path).doc.name for path in PACKAGED.paths)


def _bearer(token: str, tenant: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if tenant is not None:
        headers[TENANT_HEADER] = tenant
    return headers


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    default_tenant(hub.engine)
    return hub


def _hub_admin(hub: HubHarness) -> str:
    user = seed_user(hub, username="hal", role=Role.PENDING)
    seed_hub_admin(hub.engine, user.user_id)
    return seed_session(hub, user)


def _create(hub: HubHarness, session: str, name: str, **extra: object) -> dict[str, object]:
    resp = hub.client.post("/api/admin/tenants", json={"name": name, **extra}, headers=_bearer(session))
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_a_hub_administrator_creates_lists_and_renames_tenants(hub: HubHarness) -> None:
    session = _hub_admin(hub)
    admin = seed_user(hub, username="ada", role=Role.PENDING)

    created = _create(hub, session, "acme", admin_user_id=admin.user_id)
    renamed = hub.client.patch(
        f"/api/admin/tenants/{created['tenant_id']}", json={"name": "acme-two"}, headers=_bearer(session)
    )
    listed = hub.client.get("/api/admin/tenants", headers=_bearer(session))

    assert created["name"] == "acme" and created["state"] == "open"
    assert renamed.status_code == 200 and renamed.json() == {**created, "name": "acme-two"}
    assert listed.status_code == 200
    assert {(t["tenant_id"], t["name"], t["state"]) for t in listed.json()} == {
        (default_tenant(hub.engine), "default", "open"),
        (created["tenant_id"], "acme-two", "open"),
    }
    assert hub.hub_services.memberships.role_in(TenantId(str(created["tenant_id"])), admin.user_id) is Role.ADMIN


def test_creating_with_an_unknown_admin_answers_404_and_creates_nothing(hub: HubHarness) -> None:
    session = _hub_admin(hub)

    resp = hub.client.post(
        "/api/admin/tenants", json={"name": "acme", "admin_user_id": "usr_nobody"}, headers=_bearer(session)
    )

    assert resp.status_code == 404 and resp.json() == {"detail": "no such user"}
    assert [t["name"] for t in hub.client.get("/api/admin/tenants", headers=_bearer(session)).json()] == ["default"]


@pytest.mark.parametrize(
    "name",
    ["", "   ", "\n", " acme", "acme ", "x" * 65, "\x1cacme"],
    ids=["blank", "whitespace-only", "a-line-break", "leading", "trailing", "65-characters", "leading-separator"],
)
def test_a_name_without_a_tenant_names_shape_answers_422_on_create_and_rename(hub: HubHarness, name: str) -> None:
    session = _hub_admin(hub)
    created = _create(hub, session, "acme")

    create = hub.client.post("/api/admin/tenants", json={"name": name}, headers=_bearer(session))
    rename = hub.client.patch(
        f"/api/admin/tenants/{created['tenant_id']}", json={"name": name}, headers=_bearer(session)
    )
    listed = hub.client.get("/api/admin/tenants", headers=_bearer(session)).json()

    assert (create.status_code, rename.status_code) == (422, 422)
    assert sorted(t["name"] for t in listed) == ["acme", "default"]


def test_a_name_of_64_characters_or_with_an_inner_space_is_accepted(hub: HubHarness) -> None:
    session = _hub_admin(hub)

    longest = _create(hub, session, "x" * 64)
    renamed = hub.client.patch(
        f"/api/admin/tenants/{longest['tenant_id']}", json={"name": "acme corp"}, headers=_bearer(session)
    )

    assert longest["name"] == "x" * 64
    assert renamed.status_code == 200 and renamed.json()["name"] == "acme corp"


def test_renaming_a_tenant_that_does_not_exist_answers_404(hub: HubHarness) -> None:
    resp = hub.client.patch("/api/admin/tenants/ten_nobody", json={"name": "x"}, headers=_bearer(_hub_admin(hub)))

    assert resp.status_code == 404 and resp.json() == {"detail": "no such tenant"}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/admin/tenants", {"name": "acme"}),
        ("GET", "/api/admin/tenants", None),
        ("PATCH", "/api/admin/tenants/{tenant}", {"name": "acme"}),
        ("DELETE", "/api/admin/tenants/{tenant}", None),
        ("POST", "/api/admin/graphs/sync", None),
    ],
)
def test_a_tenant_admin_is_refused_every_tenant_administration_route(
    hub: HubHarness, method: str, path: str, body: dict[str, str] | None
) -> None:
    tenant = default_tenant(hub.engine)
    session = seed_session(hub, seed_user(hub, username="tia", role=Role.ADMIN))

    resp = hub.client.request(method, path.format(tenant=tenant), json=body, headers=_bearer(session, tenant))

    assert resp.status_code == 403
    assert [t.name for t in hub.hub_services.tenant_admin.list_tenants()] == ["default"]


def test_the_hub_graph_sync_answers_once_per_open_tenant(hub: HubHarness) -> None:
    session = _hub_admin(hub)
    created = _create(hub, session, "acme")

    resp = hub.client.post("/api/admin/graphs/sync", headers=_bearer(session))

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert sorted((t["tenant_id"], t["name"]) for t in body["tenants"]) == sorted(
        [(default_tenant(hub.engine), "default"), (created["tenant_id"], "acme")]
    )
    by_tenant = {t["tenant_id"]: t for t in body["tenants"]}
    assert sorted(e["name"] for e in by_tenant[created["tenant_id"]]["entries"]) == _PACKAGED_NAMES
    assert {e["status"] for e in by_tenant[created["tenant_id"]]["entries"]} == {"up-to-date"}


def test_a_rename_reaches_the_next_registration_of_the_tenants_runner(hub: HubHarness) -> None:
    session = _hub_admin(hub)
    created = _create(hub, session, "acme")
    tenant = TenantId(str(created["tenant_id"]))
    token = seed_runner(hub.open_services(tenant), "rn-acme-1", register=False)
    assert hub.app is not None

    renamed = hub.client.patch(f"/api/admin/tenants/{tenant}", json={"name": "acme-two"}, headers=_bearer(session))
    registered = TestClient(hub.app).post(
        "/api/fleet/runners", json={"workspace_id": "w1", "name": "r-acme"}, headers=_bearer(token)
    )

    assert renamed.status_code == 200
    assert registered.status_code == 201, registered.text
    assert (registered.json()["tenant_id"], registered.json()["tenant_name"]) == (tenant, "acme-two")


def test_the_tenant_scoped_graph_sync_stays_deprecated_under_graph_edit(hub: HubHarness) -> None:
    tenant = default_tenant(hub.engine)
    editor = seed_session(hub, seed_user(hub, username="ed", role=Role.ADMIN))
    guest = seed_session(hub, seed_user(hub, username="gus", role=Role.GUEST))

    synced = hub.client.post("/api/graphs/sync", headers=_bearer(editor, tenant))
    refused = hub.client.post("/api/graphs/sync", headers=_bearer(guest, tenant))
    operation = hub.client.get("/openapi.json").json()["paths"]["/api/graphs/sync"]["post"]

    assert synced.status_code == 200 and synced.json()["ok"] is True
    assert sorted(e["name"] for e in synced.json()["entries"]) == _PACKAGED_NAMES
    assert refused.status_code == 403
    assert operation["deprecated"] is True


def test_deleting_a_tenant_answers_200_with_the_rows_it_removed_and_410_after(hub: HubHarness) -> None:
    session = _hub_admin(hub)
    created = _create(hub, session, "acme")

    deleted = hub.client.delete(f"/api/admin/tenants/{created['tenant_id']}", headers=_bearer(session))
    again = hub.client.delete(f"/api/admin/tenants/{created['tenant_id']}", headers=_bearer(session))
    listed = hub.client.get("/api/admin/tenants", headers=_bearer(session))

    assert deleted.status_code == 200, deleted.text
    body = deleted.json()
    assert (body["tenant_id"], body["state"]) == (created["tenant_id"], "deleted")
    assert body["rows_removed"] > len(_PACKAGED_NAMES)
    assert again.status_code == 410
    assert [t["tenant_id"] for t in listed.json()] == [default_tenant(hub.engine)]


def test_a_delete_cut_off_by_its_budget_answers_202_and_the_list_shows_it_deleting(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _hub_admin(hub)
    created = _create(hub, session, "acme")
    monkeypatch.setattr(teardown, "DRAIN_PASS_ROWS", 1)

    first = hub.client.delete(f"/api/admin/tenants/{created['tenant_id']}", headers=_bearer(session))
    again = hub.client.delete(f"/api/admin/tenants/{created['tenant_id']}", headers=_bearer(session))
    listed = {t["tenant_id"]: t for t in hub.client.get("/api/admin/tenants", headers=_bearer(session)).json()}

    assert first.status_code == 202 and first.json()["state"] == "deleting"
    assert first.json()["rows_removed"] is None
    assert again.status_code == 202
    assert listed[created["tenant_id"]]["state"] == "deleting"
    assert listed[created["tenant_id"]]["closed_at"] is not None
    assert listed[default_tenant(hub.engine)]["closed_at"] is None


def test_deleting_the_only_tenant_answers_409_and_an_unknown_one_404(hub: HubHarness) -> None:
    session = _hub_admin(hub)

    only = hub.client.delete(f"/api/admin/tenants/{default_tenant(hub.engine)}", headers=_bearer(session))
    unknown = hub.client.delete("/api/admin/tenants/ten_nobody", headers=_bearer(session))

    assert only.status_code == 409 and only.json()["detail"]["code"] == "last_tenant"
    assert unknown.status_code == 404 and unknown.json() == {"detail": "no such tenant"}
