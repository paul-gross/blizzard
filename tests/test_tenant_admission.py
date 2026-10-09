"""A tenant router that admits a hub-level permission without a membership, driven through the hub app beside a
plain tenant router serving the same route.

The admitting router lets a person holding its one hub-level permission act in an open tenant they hold no
membership in, with that permission alone; a tenant that does not exist or is closed, a person without the
permission, a person naming no tenant, and every member answer exactly as on the plain router."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import pytest
import sqlalchemy as sa
from fastapi import APIRouter, Depends

from blizzard.auth_core import Permission, Role
from blizzard.hub.api.auth_session import (
    TENANT_HEADER,
    TenantBoundAdmitting,
    TenantRequest,
    require,
    require_either,
    resolve_tenant,
    tenant_bound,
)
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.config import AUTH_MODE_OAUTH
from blizzard.hub.store import schema
from blizzard.hub.tenancy import TenantId
from tests.support import HubHarness, build_hub, default_tenant, seed_hub_admin, seed_session, seed_user

pytestmark = pytest.mark.component

_ADMITTED = Permission.MEMBERSHIP_GRANT_ANY


def _scratch_router(prefix: str, bound: Any) -> APIRouter:
    router = APIRouter(prefix=prefix, dependencies=[Depends(bound)])

    @router.get("/acting")
    def acting(
        actor: Annotated[ResolvedIdentity, Depends(require_either(Permission.USER_MANAGE, _ADMITTED))],
        tenant: Annotated[TenantRequest, Depends(resolve_tenant)],
    ) -> dict[str, object]:
        return {
            "tenant": tenant.scope.tenant,
            "role": None if actor.role is None else actor.role.value,
            "permissions": sorted(actor.permissions),
        }

    @router.get("/tenant-admin")
    def tenant_admin(actor: Annotated[ResolvedIdentity, Depends(require(Permission.TENANT_ADMIN))]) -> None:
        del actor

    return router


@dataclass(frozen=True)
class _Hub:
    harness: HubHarness

    def get(self, path: str, *, session: str, tenant: str | None) -> tuple[int, object]:
        headers = {"Authorization": f"Bearer {session}"}
        if tenant is not None:
            headers[TENANT_HEADER] = tenant
        response = self.harness.client.get(path, headers=headers)
        return response.status_code, response.json()

    def both(self, route: str, *, session: str, tenant: str | None) -> tuple[tuple[int, object], tuple[int, object]]:
        """The admitting router's answer, then the plain router's, to the same call."""
        return (
            self.get(f"/scratch/admitting/{route}", session=session, tenant=tenant),
            self.get(f"/scratch/plain/{route}", session=session, tenant=tenant),
        )

    def session_of(self, username: str, role: Role, *, hub_admin: bool = False) -> str:
        user = seed_user(self.harness, username=username, role=role)
        if hub_admin:
            seed_hub_admin(self.harness.engine, user.user_id)
        return seed_session(self.harness, user)


@pytest.fixture
def hub(tmp_path: Path) -> _Hub:
    harness = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    assert harness.app is not None
    harness.app.include_router(_scratch_router("/scratch/admitting", TenantBoundAdmitting(_ADMITTED)))
    harness.app.include_router(_scratch_router("/scratch/plain", tenant_bound))
    # build_hub mounted the web frontend at "/" before these routes joined, and its catch-all answers any path it
    # reaches first whenever a built bundle is present: the mount moves back to the end, where the hub keeps it.
    harness.app.router.routes.sort(key=lambda route: getattr(route, "name", None) == "web")
    return _Hub(harness)


_NO_SUCH_TENANT = (404, {"detail": "no such tenant"})


def test_a_hub_administrator_without_a_membership_is_admitted_with_the_one_permission(hub: _Hub) -> None:
    tenant = default_tenant(hub.harness.engine)
    session = hub.session_of("hal", Role.PENDING, hub_admin=True)

    admitting, plain = hub.both("acting", session=session, tenant=tenant)

    assert admitting == (200, {"tenant": tenant, "role": None, "permissions": [_ADMITTED.value]})
    assert plain == _NO_SUCH_TENANT


def test_the_admitted_permission_is_the_only_one_the_admitted_person_holds(hub: _Hub) -> None:
    session = hub.session_of("hal", Role.PENDING, hub_admin=True)

    status, _ = hub.get("/scratch/admitting/tenant-admin", session=session, tenant=default_tenant(hub.harness.engine))

    assert status == 403


def _closed_tenant(hub: _Hub) -> TenantId:
    closed = hub.harness.add_tenant("gone")
    with hub.harness.engine.begin() as conn:
        conn.execute(
            sa.insert(schema.tenant_deletions).values(tenant_id=closed.tenant, name=closed.name, deleted_by="test")
        )
    hub.harness.hub_services.tenants.open_tenants.refresh()
    return closed.tenant


@pytest.mark.parametrize("which", ["unknown", "closed"])
def test_an_unknown_or_closed_tenant_answers_a_hub_administrator_as_the_plain_router_does(
    hub: _Hub, which: str
) -> None:
    tenant = TenantId("ten_00000000000000000000000000") if which == "unknown" else _closed_tenant(hub)
    session = hub.session_of("hal", Role.PENDING, hub_admin=True)

    admitting, plain = hub.both("acting", session=session, tenant=tenant)

    assert admitting == plain == _NO_SUCH_TENANT


def test_a_hub_administrator_naming_no_tenant_is_not_admitted(hub: _Hub) -> None:
    session = hub.session_of("hal", Role.PENDING, hub_admin=True)

    admitting, plain = hub.both("acting", session=session, tenant=None)

    assert admitting == plain == _NO_SUCH_TENANT


@pytest.mark.parametrize(
    ("role", "status"),
    [(Role.PENDING, 404), (Role.GUEST, 403), (Role.ADMIN, 200)],
)
def test_a_person_without_the_hub_permission_is_answered_as_on_the_plain_router(
    hub: _Hub, role: Role, status: int
) -> None:
    session = hub.session_of("ada", role)

    admitting, plain = hub.both("acting", session=session, tenant=default_tenant(hub.harness.engine))

    assert admitting == plain
    assert admitting[0] == status


def test_a_hub_administrator_holding_a_membership_acts_under_it(hub: _Hub) -> None:
    tenant = default_tenant(hub.harness.engine)
    session = hub.session_of("hal", Role.SUPERUSER)

    admitting, plain = hub.both("acting", session=session, tenant=tenant)

    assert admitting == plain
    assert admitting[0] == 200


def test_only_a_hub_level_permission_admits_a_non_member() -> None:
    with pytest.raises(ValueError, match="no hub-level permission"):
        TenantBoundAdmitting(Permission.USER_MANAGE)
