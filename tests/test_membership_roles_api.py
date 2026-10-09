"""What a person may do in a tenant is decided by their membership there — one session reaches every
tenant they belong to, each under its own role; a revoked membership closes its tenant alone; and the hub
administrator role opens the hub-level routes but nothing inside a tenant — against a real hub holding
two tenants under ``auth.mode = "oauth"``."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.auth_core import HUB_PERMISSIONS, Permission, Role
from blizzard.hub.api.auth_session import TENANT_HEADER
from blizzard.hub.auth.models import User
from blizzard.hub.store import schema
from blizzard.hub.tenancy import TenantId
from tests.support import HubHarness, add_tenant, build_hub, default_tenant, seed_runner, seed_session, seed_user

pytestmark = pytest.mark.component

_ALPHA_RUNNER = "rn_alpha"
_BRAVO_RUNNER = "rn_bravo"


def _two_tenants(tmp_path: Path) -> tuple[HubHarness, TenantId, TenantId]:
    """An oauth hub whose default tenant holds ``_ALPHA_RUNNER`` and whose second tenant, ``bravo``,
    holds ``_BRAVO_RUNNER`` — each runner visible only from its own tenant."""
    hub = build_hub(tmp_path, auth_mode="oauth")
    alpha = default_tenant(hub.engine)
    seed_runner(hub.open_services(alpha), _ALPHA_RUNNER)
    bravo = add_tenant(hub.engine, "bravo")
    seed_runner(hub.open_services(bravo), _BRAVO_RUNNER)
    return hub, alpha, bravo


def _set_membership(hub: HubHarness, user: User, tenant: TenantId, role: Role | None) -> None:
    """Append a membership fact for ``user`` in ``tenant``; ``None`` revokes the membership."""
    with hub.engine.begin() as conn:
        conn.execute(
            sa.insert(schema.membership_facts).values(
                tenant_id=tenant,
                user_id=user.user_id,
                role=None if role is None else role.value,
                set_at=hub.clock.now(),
                set_by="test",
            )
        )


def _grant_hub_admin(hub: HubHarness, user: User) -> None:
    with hub.engine.begin() as conn:
        conn.execute(
            sa.insert(schema.hub_admin_facts).values(
                user_id=user.user_id, granted=True, set_at=hub.clock.now(), set_by="test"
            )
        )


def _cookie(hub: HubHarness, user: User) -> dict[str, str]:
    return {"Cookie": f"bz_session={seed_session(hub, user)}"}


def _in(tenant: TenantId, cookie: dict[str, str]) -> dict[str, str]:
    return {**cookie, TENANT_HEADER: tenant}


def _permissions(hub: HubHarness, headers: dict[str, str]) -> set[str]:
    resp = hub.client.get("/api/me", headers=headers)
    assert resp.status_code == 200, resp.text
    return set(resp.json()["permissions"])


def test_one_session_reaches_each_tenant_it_holds_a_membership_in(tmp_path: Path) -> None:
    hub, alpha, bravo = _two_tenants(tmp_path)
    ada = seed_user(hub, username="ada", role=Role.GUEST)
    _set_membership(hub, ada, bravo, Role.GUEST)
    cookie = _cookie(hub, ada)

    in_alpha = hub.client.get("/api/runners", headers=_in(alpha, cookie))
    in_bravo = hub.client.get("/api/runners", headers=_in(bravo, cookie))

    assert in_alpha.status_code == in_bravo.status_code == 200
    assert [r["runner_id"] for r in in_alpha.json()["runners"]] == [_ALPHA_RUNNER]
    assert [r["runner_id"] for r in in_bravo.json()["runners"]] == [_BRAVO_RUNNER]


def test_a_members_permissions_are_those_of_their_role_in_the_named_tenant(tmp_path: Path) -> None:
    hub, alpha, bravo = _two_tenants(tmp_path)
    ada = seed_user(hub, username="ada", role=Role.ADMIN)
    _set_membership(hub, ada, bravo, Role.GUEST)
    cookie = _cookie(hub, ada)

    assert hub.client.get("/api/users", headers=_in(alpha, cookie)).status_code == 200
    assert hub.client.get("/api/users", headers=_in(bravo, cookie)).status_code == 403
    assert hub.client.get(f"/api/runners/{_BRAVO_RUNNER}", headers=_in(bravo, cookie)).status_code == 200
    assert Permission.USER_MANAGE in _permissions(hub, _in(alpha, cookie))
    assert _permissions(hub, _in(bravo, cookie)) == {Permission.FLEET_VIEW}


def test_a_revoked_membership_answers_404_in_its_tenant_alone(tmp_path: Path) -> None:
    hub, alpha, bravo = _two_tenants(tmp_path)
    ada = seed_user(hub, username="ada", role=Role.CONTRIBUTOR)
    _set_membership(hub, ada, bravo, Role.CONTRIBUTOR)
    cookie = _cookie(hub, ada)
    assert hub.client.get("/api/runners", headers=cookie).status_code == 409

    _set_membership(hub, ada, bravo, None)

    revoked = hub.client.get("/api/runners", headers=_in(bravo, cookie))
    unknown = hub.client.get("/api/runners", headers=_in(TenantId("ten_00000000000000000000000000"), cookie))
    assert revoked.status_code == 404
    assert revoked.json() == unknown.json()
    assert hub.client.get("/api/runners", headers=_in(alpha, cookie)).status_code == 200
    headerless = hub.client.get("/api/runners", headers=cookie)
    assert headerless.status_code == 200
    assert [r["runner_id"] for r in headerless.json()["runners"]] == [_ALPHA_RUNNER]


def test_a_role_set_through_the_users_route_holds_in_the_named_tenant_only(tmp_path: Path) -> None:
    hub, alpha, bravo = _two_tenants(tmp_path)
    root = seed_user(hub, username="root", role=Role.SUPERUSER)
    _set_membership(hub, root, bravo, Role.ADMIN)
    ada = seed_user(hub, username="ada", role=Role.GUEST)
    _set_membership(hub, ada, bravo, Role.GUEST)
    root_cookie, ada_cookie = _cookie(hub, root), _cookie(hub, ada)

    granted = hub.client.post(
        f"/api/users/{ada.user_id}/role", json={"role": Role.ADMIN.value}, headers=_in(bravo, root_cookie)
    )

    assert granted.status_code == 200, granted.text
    assert granted.json()["role"] == Role.ADMIN.value
    assert hub.client.get("/api/users", headers=_in(bravo, ada_cookie)).status_code == 200
    assert hub.client.get("/api/users", headers=_in(alpha, ada_cookie)).status_code == 403


def test_a_revocation_through_the_users_route_closes_that_tenant_alone(tmp_path: Path) -> None:
    hub, alpha, bravo = _two_tenants(tmp_path)
    root = seed_user(hub, username="root", role=Role.SUPERUSER)
    _set_membership(hub, root, bravo, Role.ADMIN)
    ada = seed_user(hub, username="ada", role=Role.CONTRIBUTOR)
    _set_membership(hub, ada, bravo, Role.CONTRIBUTOR)
    ada_cookie = _cookie(hub, ada)

    revoked = hub.client.post(
        f"/api/users/{ada.user_id}/role", json={"role": Role.PENDING.value}, headers=_in(bravo, _cookie(hub, root))
    )

    assert revoked.status_code == 200, revoked.text
    assert hub.client.get("/api/runners", headers=_in(bravo, ada_cookie)).status_code == 404
    assert hub.client.get("/api/runners", headers=_in(alpha, ada_cookie)).status_code == 200


def test_a_hub_administrator_without_a_membership_reaches_the_hub_level_routes_alone(tmp_path: Path) -> None:
    hub, alpha, bravo = _two_tenants(tmp_path)
    root = seed_user(hub, username="root", role=Role.PENDING)
    _grant_hub_admin(hub, root)
    cookie = _cookie(hub, root)

    assert hub.client.get("/api/egress/status", headers=cookie).status_code == 200
    assert hub.client.get("/api/traces/status", headers=_in(bravo, cookie)).status_code == 200
    assert hub.client.get("/api/runners", headers=cookie).status_code == 404
    assert hub.client.get("/api/runners", headers=_in(alpha, cookie)).status_code == 404
    assert hub.client.get("/api/runners", headers=_in(bravo, cookie)).status_code == 404
    assert _permissions(hub, cookie) == set(HUB_PERMISSIONS)


def test_the_hub_administrator_role_adds_no_permission_inside_a_tenant(tmp_path: Path) -> None:
    hub, _, bravo = _two_tenants(tmp_path)
    root = seed_user(hub, username="root", role=Role.SUPERUSER)
    _set_membership(hub, root, bravo, Role.GUEST)
    cookie = _in(bravo, _cookie(hub, root))

    assert hub.client.get(f"/api/runners/{_BRAVO_RUNNER}", headers=cookie).status_code == 200
    assert hub.client.get("/api/users", headers=cookie).status_code == 403
    assert hub.client.get("/api/egress/status", headers=cookie).status_code == 200
    assert _permissions(hub, cookie) == {Permission.FLEET_VIEW} | HUB_PERMISSIONS


def test_a_tenant_admin_is_refused_the_hub_level_routes_whichever_tenant_is_named(tmp_path: Path) -> None:
    hub, alpha, bravo = _two_tenants(tmp_path)
    ada = seed_user(hub, username="ada", role=Role.ADMIN)
    _set_membership(hub, ada, bravo, Role.ADMIN)
    cookie = _cookie(hub, ada)

    for headers in (cookie, _in(alpha, cookie), _in(bravo, cookie)):
        assert hub.client.get("/api/egress/status", headers=headers).status_code == 403
        assert hub.client.get("/api/traces/status", headers=headers).status_code == 403
