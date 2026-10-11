"""``GET /api/admin/hub-admins`` and ``PUT``/``DELETE /api/admin/hub-admins/{user_id}`` — the hub administrators.

Only a hub administrator lists, grants and revokes the role; any number of users hold it; the last one is kept; and a
tenant's admin reaches none of it, whichever tenant they name."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.auth_core import Role
from blizzard.hub.api.auth_session import TENANT_HEADER
from blizzard.hub.auth.models import User
from blizzard.hub.tenancy import TenantId
from tests.support import HubHarness, build_hub, default_tenant, seed_membership, seed_session, seed_user

pytestmark = pytest.mark.component


def _as(hub: HubHarness, user: User, tenant: TenantId | None = None) -> dict[str, str]:
    headers = {"Cookie": f"bz_session={seed_session(hub, user)}"}
    return headers if tenant is None else {**headers, TENANT_HEADER: tenant}


def _holders(hub: HubHarness, actor: User) -> list[str]:
    resp = hub.client.get("/api/admin/hub-admins", headers=_as(hub, actor))
    assert resp.status_code == 200, resp.text
    return [h["username"] for h in resp.json()]


def test_a_hub_administrator_grants_another_and_both_hold_the_role(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    root = seed_user(hub, username="root", role=Role.SUPERUSER, email="root@example.com")
    ada = seed_user(hub, username="ada", role=Role.GUEST)

    granted = hub.client.put(f"/api/admin/hub-admins/{ada.user_id}", headers=_as(hub, root))

    assert granted.status_code == 200, granted.text
    assert granted.json() == {"user_id": ada.user_id, "username": "ada", "display_name": "ada", "email": None}
    assert sorted(_holders(hub, root)) == ["ada", "root"]
    assert hub.client.put(f"/api/admin/hub-admins/{ada.user_id}", headers=_as(hub, root)).status_code == 200
    assert sorted(_holders(hub, ada)) == ["ada", "root"]


def test_a_hub_administrator_revokes_another(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    root = seed_user(hub, username="root", role=Role.SUPERUSER)
    ada = seed_user(hub, username="ada", role=Role.SUPERUSER)

    assert hub.client.delete(f"/api/admin/hub-admins/{ada.user_id}", headers=_as(hub, root)).status_code == 204
    assert _holders(hub, root) == ["root"]


def test_the_last_hub_administrator_is_kept(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    root = seed_user(hub, username="root", role=Role.SUPERUSER)

    resp = hub.client.delete(f"/api/admin/hub-admins/{root.user_id}", headers=_as(hub, root))

    assert resp.status_code == 409, resp.text
    assert _holders(hub, root) == ["root"]


def test_granting_a_user_that_does_not_exist_answers_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    root = seed_user(hub, username="root", role=Role.SUPERUSER)

    assert hub.client.put("/api/admin/hub-admins/usr_missing", headers=_as(hub, root)).status_code == 404


def test_a_tenant_admin_reaches_no_hub_administration_whichever_tenant_they_name(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    ada = seed_user(hub, username="ada", role=Role.ADMIN)
    seed_user(hub, username="root", role=Role.SUPERUSER)
    other = hub.add_tenant("other")
    seed_membership(hub.engine, other.tenant, ada.user_id, Role.ADMIN)
    stranger = hub.add_tenant("stranger")

    for tenant in (None, default_tenant(hub.engine), other.tenant):
        headers = _as(hub, ada, tenant)
        assert hub.client.get("/api/admin/hub-admins", headers=headers).status_code == 403
        assert hub.client.put(f"/api/admin/hub-admins/{ada.user_id}", headers=headers).status_code == 403
        assert hub.client.delete(f"/api/admin/hub-admins/{ada.user_id}", headers=headers).status_code == 403
    assert hub.client.get("/api/members", headers=_as(hub, ada, stranger.tenant)).status_code == 404


def test_a_person_with_no_membership_is_refused_hub_administration(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    newcomer = seed_user(hub, username="newcomer", role=Role.PENDING)

    assert hub.client.get("/api/admin/hub-admins", headers=_as(hub, newcomer)).status_code == 403
