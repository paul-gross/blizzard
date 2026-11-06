"""How a request resolves the tenant it acts in — a machine credential first, then the
``X-Blizzard-Tenant`` header checked against the caller's memberships, then the caller's one
membership, else ``409 tenant_required`` — against a real hub holding two tenants."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from blizzard.auth_core import Role
from blizzard.hub.api.auth_session import TENANT_HEADER
from blizzard.hub.auth.models import User
from blizzard.hub.store import schema
from blizzard.hub.tenancy import TenantId
from tests.support.scaffolding import (
    HubHarness,
    add_tenant,
    build_hub,
    default_tenant,
    runner_token,
    seed_runner,
    seed_session,
)
from tests.support.scaffolding import seed_user as _seed_user

_RUNNER = "rn_bravo"


def _second_tenant(hub: HubHarness) -> TenantId:
    """A second tenant holding one runner, ``_RUNNER``, that the default tenant does not hold."""
    tenant = add_tenant(hub.engine, "bravo")
    seed_runner(hub.open_services(tenant), _RUNNER)
    return tenant


def _join(hub: HubHarness, user: User, tenant: TenantId, role: Role) -> None:
    with hub.engine.begin() as conn:
        conn.execute(
            sa.insert(schema.membership_facts).values(
                tenant_id=tenant, user_id=user.user_id, role=role.value, set_at=datetime.now(UTC), set_by="test"
            )
        )


def _person(hub: HubHarness, role: Role = Role.ADMIN) -> tuple[User, dict[str, str]]:
    user = _seed_user(hub, username="ada", role=role)
    return user, {"Cookie": f"bz_session={seed_session(hub, user)}"}


def _runner_in(hub: HubHarness, tenant: TenantId | None, headers: dict[str, str]) -> int:
    if tenant is not None:
        headers = {**headers, TENANT_HEADER: tenant}
    return hub.client.get(f"/api/runners/{_RUNNER}", headers=headers).status_code


def test_one_membership_needs_no_header(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    _, cookie = _person(hub)

    assert hub.client.get("/api/chunks", headers=cookie).status_code == 200


def test_several_memberships_and_no_header_answer_409_tenant_required(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    second = _second_tenant(hub)
    user, cookie = _person(hub)
    _join(hub, user, second, Role.GUEST)

    resp = hub.client.get("/api/chunks", headers=cookie)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "tenant_required"


def test_the_header_picks_among_memberships_and_serves_only_that_tenant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    second = _second_tenant(hub)
    user, cookie = _person(hub)
    _join(hub, user, second, Role.GUEST)

    assert _runner_in(hub, second, cookie) == 200
    assert _runner_in(hub, default_tenant(hub.engine), cookie) == 404


def test_a_tenant_the_caller_cannot_enter_and_one_that_does_not_exist_answer_the_same_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    second = _second_tenant(hub)
    _, cookie = _person(hub)

    not_a_member = hub.client.get("/api/chunks", headers={**cookie, TENANT_HEADER: second})
    unknown = hub.client.get("/api/chunks", headers={**cookie, TENANT_HEADER: "ten_00000000000000000000000000"})
    by_name = hub.client.get("/api/chunks", headers={**cookie, TENANT_HEADER: "bravo"})

    assert not_a_member.status_code == unknown.status_code == by_name.status_code == 404
    assert not_a_member.json() == unknown.json() == by_name.json()


def test_a_hub_administrator_enters_a_tenant_only_through_a_membership(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    second = _second_tenant(hub)
    _, cookie = _person(hub, Role.SUPERUSER)

    assert _runner_in(hub, second, cookie) == 404


def test_a_person_with_no_membership_answers_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    _, cookie = _person(hub, Role.PENDING)

    assert hub.client.get("/api/chunks", headers=cookie).status_code == 404


def test_a_header_without_a_session_answers_401(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")

    resp = hub.client.get("/api/chunks", headers={TENANT_HEADER: default_tenant(hub.engine)})

    assert resp.status_code == 401


def test_a_runner_token_decides_its_tenant_and_a_disagreeing_header_answers_400(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    second = _second_tenant(hub)
    bearer = {"Authorization": f"Bearer {runner_token(_RUNNER)}"}
    url = f"/api/fleet/runners/{_RUNNER}"

    assert hub.client.get(url, headers=bearer).status_code == 200
    assert hub.client.get(url, headers={**bearer, TENANT_HEADER: second}).status_code == 200
    disagreeing = hub.client.get(url, headers={**bearer, TENANT_HEADER: default_tenant(hub.engine)})
    assert disagreeing.status_code == 400


def test_under_auth_none_the_operator_acts_in_whichever_tenant_is_named(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    second = _second_tenant(hub)
    hub.hub_services.tenants.open_tenants.refresh()

    assert hub.client.get("/api/chunks").status_code == 409
    assert _runner_in(hub, second, {}) == 200
    assert _runner_in(hub, default_tenant(hub.engine), {}) == 404
