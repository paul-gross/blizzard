"""The harness's seeding helpers for memberships, hub administrators, invitations and further tenants each write
what the hub itself would read back: a membership in the named tenant, a hub administrator with no membership, an
invitation stored by its token's hash, and a tenant whose client acts in it alone."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.auth_core import Role
from blizzard.hub.auth.internal.hub_admin_repository import HubAdminRepository
from blizzard.hub.store import schema
from blizzard.hub.tenancy import TenantClosed
from tests.support import (
    build_hub,
    close_tenant,
    default_tenant,
    hub_level_connections,
    scoped_connections,
    seed_hub_admin,
    seed_invitation,
    seed_membership,
    seed_user,
)

pytestmark = pytest.mark.component


def test_a_seeded_membership_is_the_role_the_hub_reads_in_that_tenant_only(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    other = hub.add_tenant("beta")
    user = seed_user(hub, username="ada", role=Role.PENDING)

    seed_membership(hub.engine, other.tenant, user.user_id, Role.CONTRIBUTOR)

    memberships = hub.hub_services.memberships
    assert memberships.role_in(other.tenant, user.user_id) is Role.CONTRIBUTOR
    assert memberships.role_in(default_tenant(hub.engine), user.user_id) is None


def test_a_seeded_hub_administrator_holds_no_membership(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    user = seed_user(hub, username="ada", role=Role.PENDING)

    seed_hub_admin(hub.engine, user.user_id)

    assert HubAdminRepository(hub_level_connections(hub.engine)).is_hub_admin(user.user_id)
    assert hub.hub_services.memberships.memberships_for(user.user_id) == []


def test_a_seeded_invitation_stores_its_token_only_as_a_hash(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    tenant = default_tenant(hub.engine)

    invitation_id, token = seed_invitation(hub.engine, tenant, "Ada@Example.com", Role.ADMIN, created_by="usr_x")

    with hub.engine.connect() as conn:
        row = conn.execute(
            sa.select(schema.invitations).where(
                schema.invitations.c.tenant_id == tenant, schema.invitations.c.invitation_id == invitation_id
            )
        ).one()
    assert invitation_id.startswith("inv_")
    assert (row.email, row.role, row.created_by) == ("ada@example.com", "admin", "usr_x")
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in tuple(row)
    assert row.expires_at - row.created_at == timedelta(days=7)


def test_an_added_tenants_client_acts_in_that_tenant_and_seeds_prefixed_runners(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    other = hub.add_tenant("beta")

    assert other.client.get("/api/fleet/summary").status_code == 200

    assert other.client.default_runner_id == other.runner_id("r1") == "beta-r1"
    assert other.services.registry.get_runner("beta-r1") is not None
    assert hub.services.registry.get_runner("beta-r1") is None
    with pytest.raises(AssertionError, match="unique across the hub"):
        other.client.get("/api/fleet/summary", params={"runner_id": "r1"})


def test_an_added_tenant_leaves_a_headerless_call_naming_no_tenant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    hub.add_tenant("beta")

    response = hub.client.get("/api/runners")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "tenant_required"


def test_a_closed_tenant_is_recorded_under_its_name_and_refuses_its_scoped_writes(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    tenant = default_tenant(hub.engine)
    other = hub.add_tenant("beta")

    close_tenant(hub.engine, other.tenant, by="usr_closer")

    deletions = schema.tenant_deletions
    with hub.engine.connect() as conn:
        rows = conn.execute(sa.select(deletions.c.tenant_id, deletions.c.name, deletions.c.deleted_by)).all()
    assert [tuple(row) for row in rows] == [(other.tenant, "beta", "usr_closer")]
    with pytest.raises(TenantClosed), scoped_connections(hub.engine, other.tenant).write("test"):
        pass
    with scoped_connections(hub.engine, tenant).write("test"):
        pass
