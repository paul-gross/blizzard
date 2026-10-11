"""``GET /api/me`` names every tenant the caller holds a membership in, each with its role and the permissions it
holds there, and whether the caller is a hub administrator; its ``role`` and ``permissions`` are those of the tenant
the request resolves — the header's, else the only membership's — and null and hub-level only otherwise. A header
naming a tenant the caller cannot enter resolves nothing here rather than answering 404."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.auth_core import HUB_PERMISSIONS, Role, expand
from blizzard.hub.api.auth_session import TENANT_HEADER
from blizzard.hub.auth.models import User
from blizzard.hub.config import AUTH_MODE_NONE
from blizzard.hub.tenancy import TenantId
from tests.support import (
    HubHarness,
    add_tenant,
    build_hub,
    close_tenant,
    default_tenant,
    seed_hub_admin,
    seed_membership,
    seed_session,
    seed_user,
)

pytestmark = pytest.mark.component

_UNKNOWN = "ten_00000000000000000000000000"


def _cookie(hub: HubHarness, user: User) -> dict[str, str]:
    return {"Cookie": f"bz_session={seed_session(hub, user)}"}


def _me(hub: HubHarness, headers: dict[str, str], tenant: TenantId | str | None = None) -> dict:  # type: ignore[type-arg]
    named = headers if tenant is None else {**headers, TENANT_HEADER: tenant}
    resp = hub.client.get("/api/me", headers=named)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _permissions(role: Role | None, *, hub_admin: bool) -> list[str]:
    held = (set() if role is None else set(expand(role))) | (set(HUB_PERMISSIONS) if hub_admin else set())
    return sorted(permission.value for permission in held)


def _two_tenants(tmp_path: Path) -> tuple[HubHarness, TenantId, TenantId]:
    hub = build_hub(tmp_path, auth_mode="oauth")
    alpha = default_tenant(hub.engine)
    return hub, alpha, add_tenant(hub.engine, "bravo")


def test_every_membership_is_listed_with_its_own_role_and_permissions(tmp_path: Path) -> None:
    hub, alpha, bravo = _two_tenants(tmp_path)
    ada = seed_user(hub, username="ada", role=Role.GUEST)
    seed_membership(hub.engine, bravo, ada.user_id, Role.ADMIN)
    seed_hub_admin(hub.engine, ada.user_id)

    body = _me(hub, _cookie(hub, ada))

    assert body["hub_admin"] is True
    alpha_name = hub.hub_services.tenants.open_tenants.name(alpha)
    assert body["tenants"] == sorted(
        [
            {
                "tenant_id": alpha,
                "name": alpha_name,
                "role": "guest",
                "permissions": _permissions(Role.GUEST, hub_admin=True),
            },
            {
                "tenant_id": bravo,
                "name": "bravo",
                "role": "admin",
                "permissions": _permissions(Role.ADMIN, hub_admin=True),
            },
        ],
        key=lambda tenant: tenant["tenant_id"],
    )


def test_several_memberships_and_no_header_resolve_no_tenant_and_only_hub_permissions(tmp_path: Path) -> None:
    hub, _, bravo = _two_tenants(tmp_path)
    ada = seed_user(hub, username="ada", role=Role.GUEST)
    seed_membership(hub.engine, bravo, ada.user_id, Role.ADMIN)
    seed_hub_admin(hub.engine, ada.user_id)

    body = _me(hub, _cookie(hub, ada))

    assert (body["role"], body["permissions"]) == (None, _permissions(None, hub_admin=True))


def test_the_header_fills_role_and_permissions_for_the_tenant_it_names(tmp_path: Path) -> None:
    hub, alpha, bravo = _two_tenants(tmp_path)
    ada = seed_user(hub, username="ada", role=Role.GUEST)
    seed_membership(hub.engine, bravo, ada.user_id, Role.CONTRIBUTOR)
    cookie = _cookie(hub, ada)

    in_alpha = _me(hub, cookie, alpha)
    in_bravo = _me(hub, cookie, bravo)

    assert (in_alpha["role"], in_alpha["permissions"]) == ("guest", _permissions(Role.GUEST, hub_admin=False))
    assert (in_bravo["role"], in_bravo["permissions"]) == (
        "contributor",
        _permissions(Role.CONTRIBUTOR, hub_admin=False),
    )


def test_one_membership_needs_no_header(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    grace = seed_user(hub, username="grace", role=Role.CONTRIBUTOR)

    body = _me(hub, _cookie(hub, grace))

    assert (body["role"], body["permissions"]) == ("contributor", _permissions(Role.CONTRIBUTOR, hub_admin=False))
    assert [tenant["tenant_id"] for tenant in body["tenants"]] == [default_tenant(hub.engine)]
    assert body["hub_admin"] is False


@pytest.mark.parametrize("named", ["other", _UNKNOWN], ids=["a-tenant-the-caller-is-not-in", "no-such-tenant"])
def test_a_header_naming_a_tenant_the_caller_cannot_enter_resolves_nothing(tmp_path: Path, named: str) -> None:
    hub, _, bravo = _two_tenants(tmp_path)
    grace = seed_user(hub, username="grace", role=Role.CONTRIBUTOR)

    body = _me(hub, _cookie(hub, grace), bravo if named == "other" else named)

    assert (body["role"], body["permissions"]) == (None, [])
    assert len(body["tenants"]) == 1


def test_a_closed_tenants_membership_is_neither_listed_nor_resolved(tmp_path: Path) -> None:
    hub, alpha, bravo = _two_tenants(tmp_path)
    ada = seed_user(hub, username="ada", role=Role.GUEST)
    seed_membership(hub.engine, bravo, ada.user_id, Role.ADMIN)
    close_tenant(hub.engine, bravo)
    hub.hub_services.tenants.open_tenants.refresh()
    cookie = _cookie(hub, ada)

    headerless = _me(hub, cookie)

    assert [tenant["tenant_id"] for tenant in headerless["tenants"]] == [alpha]
    assert headerless["role"] is None
    assert _me(hub, cookie, bravo)["role"] is None
    assert _me(hub, cookie, alpha)["role"] == "guest"


def test_a_person_with_no_membership_reaches_me_and_no_tenant_route(tmp_path: Path) -> None:
    hub, _, bravo = _two_tenants(tmp_path)
    newcomer = seed_user(hub, username="newcomer", role=Role.PENDING)
    cookie = _cookie(hub, newcomer)

    body = _me(hub, cookie)

    assert (body["role"], body["permissions"], body["tenants"], body["hub_admin"]) == (None, [], [], False)
    assert hub.client.get("/api/chunks", headers=cookie).status_code == 404
    assert hub.client.get("/api/chunks", headers={**cookie, TENANT_HEADER: bravo}).status_code == 404


def test_under_auth_none_the_operator_is_an_admin_of_every_open_tenant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_NONE)
    alpha = default_tenant(hub.engine)

    alone = _me(hub, {})
    assert (alone["role"], alone["hub_admin"]) == ("admin", True)
    assert [(tenant["tenant_id"], tenant["role"]) for tenant in alone["tenants"]] == [(alpha, "admin")]

    bravo = add_tenant(hub.engine, "bravo")
    hub.hub_services.tenants.open_tenants.refresh()

    both = _me(hub, {})
    assert [tenant["tenant_id"] for tenant in both["tenants"]] == sorted([alpha, bravo])
    assert (both["role"], both["permissions"]) == (None, _permissions(None, hub_admin=True))
    assert _me(hub, {}, bravo)["role"] == "admin"
