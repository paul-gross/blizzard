"""``GET /api/members`` and ``PUT``/``DELETE /api/members/{user_id}`` — a tenant's members, managed in the request's
tenant.

A tenant's admin lists, grants, changes and revokes its members — ``admin`` included — but never their own role, never
a user who is no member (answered exactly as a user that does not exist), and never the tenant's last admin away. A hub
administrator grants and revokes in any tenant under ``membership:grant-any`` without a membership of their own, yet
lists members only through one."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.auth_core import Role
from blizzard.hub.api.auth_session import TENANT_HEADER
from blizzard.hub.auth.models import User
from blizzard.hub.store import schema
from blizzard.hub.tenancy import TenantId
from tests.support import (
    HubHarness,
    build_hub,
    default_tenant,
    seed_hub_admin,
    seed_membership,
    seed_session,
    seed_user,
)

pytestmark = pytest.mark.component


def _as(hub: HubHarness, user: User, tenant: TenantId | None = None) -> dict[str, str]:
    headers = {"Cookie": f"bz_session={seed_session(hub, user)}"}
    return headers if tenant is None else {**headers, TENANT_HEADER: tenant}


def _hub_admin(hub: HubHarness) -> User:
    """A hub administrator holding no membership anywhere."""
    root = seed_user(hub, username="root", role=Role.PENDING)
    seed_hub_admin(hub.engine, root.user_id)
    return root


def _facts(hub: HubHarness) -> int:
    with hub.engine.connect() as conn:
        return conn.execute(sa.select(sa.func.count()).select_from(schema.membership_facts)).scalar_one()


def test_the_listing_holds_the_request_tenants_members_alone(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    ada = seed_user(hub, username="ada", role=Role.ADMIN, email="ada@example.com")
    grace = seed_user(hub, username="grace", role=Role.GUEST)
    seed_user(hub, username="newcomer", role=Role.PENDING)
    other = hub.add_tenant("other")
    outsider = seed_user(hub, username="alan", role=Role.PENDING)
    seed_membership(hub.engine, other.tenant, outsider.user_id, Role.ADMIN)
    seed_hub_admin(hub.engine, grace.user_id)

    resp = hub.client.get("/api/members", headers=_as(hub, ada, default_tenant(hub.engine)))

    assert resp.status_code == 200, resp.text
    assert {(m["username"], m["role"], m["hub_admin"]) for m in resp.json()} == {
        ("ada", "admin", False),
        ("grace", "guest", True),
    }
    listed = {m["username"]: m for m in resp.json()}
    assert listed["ada"]["email"] == "ada@example.com"
    assert (listed["ada"]["assignable_roles"], listed["ada"]["revocable"]) == ([], False)
    assert (listed["grace"]["assignable_roles"], listed["grace"]["revocable"]) == (
        ["guest", "contributor", "admin"],
        True,
    )


def test_listing_takes_user_manage_and_a_membership(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    tenant = default_tenant(hub.engine)
    contributor = seed_user(hub, username="ada", role=Role.CONTRIBUTOR)

    assert hub.client.get("/api/members", headers=_as(hub, contributor, tenant)).status_code == 403
    assert hub.client.get("/api/members", headers=_as(hub, _hub_admin(hub), tenant)).status_code == 404


def test_a_tenant_admin_grants_admin(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    ada = seed_user(hub, username="ada", role=Role.ADMIN)
    grace = seed_user(hub, username="grace", role=Role.CONTRIBUTOR)

    resp = hub.client.put(f"/api/members/{grace.user_id}", json={"role": "admin"}, headers=_as(hub, ada))

    assert resp.status_code == 200, resp.text
    assert (resp.json()["role"], resp.json()["revocable"]) == ("admin", True)
    assert hub.hub_services.memberships.role_in(default_tenant(hub.engine), grace.user_id) is Role.ADMIN


def test_nobody_changes_their_own_role(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    seed_user(hub, username="alan", role=Role.ADMIN)
    ada = seed_user(hub, username="ada", role=Role.ADMIN)

    assert (
        hub.client.put(f"/api/members/{ada.user_id}", json={"role": "guest"}, headers=_as(hub, ada)).status_code == 403
    )
    assert hub.client.delete(f"/api/members/{ada.user_id}", headers=_as(hub, ada)).status_code == 403


def test_a_non_member_answers_exactly_as_a_user_that_does_not_exist(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    tenant = default_tenant(hub.engine)
    ada = seed_user(hub, username="ada", role=Role.ADMIN)
    other = hub.add_tenant("other")
    elsewhere = seed_user(hub, username="alan", role=Role.PENDING)
    seed_membership(hub.engine, other.tenant, elsewhere.user_id, Role.GUEST)
    headers = _as(hub, ada, tenant)
    before = _facts(hub)

    answers = [
        hub.client.put(f"/api/members/{user_id}", json={"role": "guest"}, headers=headers)
        for user_id in (elsewhere.user_id, "usr_01KXZZZZZZZZZZZZZZZZZZZZZZ", "alan")
    ] + [hub.client.delete(f"/api/members/{user_id}", headers=headers) for user_id in (elsewhere.user_id, "usr_none")]

    assert {(a.status_code, a.text) for a in answers} == {(404, '{"detail":"no such user"}')}
    assert _facts(hub) == before


def test_a_tenant_admin_reaches_no_other_tenant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    ada = seed_user(hub, username="ada", role=Role.ADMIN)
    guest_there = hub.add_tenant("guest-there")
    stranger_there = hub.add_tenant("stranger-there")
    seed_membership(hub.engine, guest_there.tenant, ada.user_id, Role.GUEST)
    grace = seed_user(hub, username="grace", role=Role.PENDING)
    seed_membership(hub.engine, guest_there.tenant, grace.user_id, Role.GUEST)
    seed_membership(hub.engine, stranger_there.tenant, grace.user_id, Role.GUEST)

    put = {"json": {"role": "contributor"}}
    assert (
        hub.client.put(f"/api/members/{grace.user_id}", headers=_as(hub, ada, guest_there.tenant), **put).status_code
        == 403
    )
    assert (
        hub.client.put(f"/api/members/{grace.user_id}", headers=_as(hub, ada, stranger_there.tenant), **put).status_code
        == 404
    )
    assert hub.client.get("/api/members", headers=_as(hub, ada, stranger_there.tenant)).status_code == 404


def test_a_hub_administrator_grants_and_revokes_in_a_tenant_they_hold_no_membership_in(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    other = hub.add_tenant("other")
    root = _hub_admin(hub)
    grace = seed_user(hub, username="grace", role=Role.PENDING)
    headers = _as(hub, root, other.tenant)

    granted = hub.client.put(f"/api/members/{grace.user_id}", json={"role": "contributor"}, headers=headers)
    assert granted.status_code == 200, granted.text
    assert hub.hub_services.memberships.role_in(other.tenant, grace.user_id) is Role.CONTRIBUTOR
    assert hub.hub_services.memberships.role_in(default_tenant(hub.engine), grace.user_id) is None

    revoked = hub.client.delete(f"/api/members/{grace.user_id}", headers=headers)
    assert revoked.status_code == 204, revoked.text
    assert hub.hub_services.memberships.role_in(other.tenant, grace.user_id) is None


def test_a_hub_administrator_naming_no_open_tenant_is_answered_as_anyone(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    root = _hub_admin(hub)
    grace = seed_user(hub, username="grace", role=Role.PENDING)

    answers = {
        hub.client.put(f"/api/members/{grace.user_id}", json={"role": "guest"}, headers=_as(hub, root, tenant)).text
        for tenant in (TenantId("ten_01KXZZZZZZZZZZZZZZZZZZZZZZ"), TenantId("not-a-tenant"))
    }
    assert answers == {'{"detail":"no such tenant"}'}


def test_a_revoked_member_can_no_longer_enter_the_tenant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    ada = seed_user(hub, username="ada", role=Role.ADMIN)
    grace = seed_user(hub, username="grace", role=Role.GUEST)
    grace_headers = _as(hub, grace)

    assert hub.client.delete(f"/api/members/{grace.user_id}", headers=_as(hub, ada)).status_code == 204
    assert hub.client.get("/api/chunks", headers=grace_headers).status_code == 404


def test_a_grant_of_the_role_in_force_writes_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    ada = seed_user(hub, username="ada", role=Role.ADMIN)
    grace = seed_user(hub, username="grace", role=Role.CONTRIBUTOR)
    before = _facts(hub)

    resp = hub.client.put(f"/api/members/{grace.user_id}", json={"role": "contributor"}, headers=_as(hub, ada))

    assert (resp.status_code, resp.json()["role"]) == (200, "contributor")
    assert _facts(hub) == before
    assert hub.hub_services.auth_facts.list_recent() == []


@pytest.mark.parametrize(("method", "body"), [("PUT", {"role": "guest"}), ("DELETE", None)])
def test_the_last_admin_is_kept(tmp_path: Path, method: str, body: dict[str, str] | None) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    ada = seed_user(hub, username="ada", role=Role.ADMIN)
    root = _hub_admin(hub)

    resp = hub.client.request(
        method, f"/api/members/{ada.user_id}", json=body, headers=_as(hub, root, default_tenant(hub.engine))
    )

    assert resp.status_code == 409, resp.text
    assert hub.hub_services.memberships.role_in(default_tenant(hub.engine), ada.user_id) is Role.ADMIN


@pytest.mark.parametrize("role", ["pending", "superuser", "wizard"])
def test_only_a_membership_role_is_granted(tmp_path: Path, role: str) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    ada = seed_user(hub, username="ada", role=Role.ADMIN)
    grace = seed_user(hub, username="grace", role=Role.GUEST)

    assert (
        hub.client.put(f"/api/members/{grace.user_id}", json={"role": role}, headers=_as(hub, ada)).status_code == 422
    )
