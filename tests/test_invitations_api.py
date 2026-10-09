"""``POST/GET /api/invitations`` and ``POST /api/invitations/{invitation_id}/revocations``: a tenant's admins issue,
list and revoke its invitations, and the hub administrator does so in any tenant without a membership there.

An invitation is stored with its address lowercased and its token only as a hash; the token is answered once, when the
invitation is issued, and never again. It lives 1 to 30 whole days, 7 unless the issuer names another."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from blizzard.auth_core import Role
from blizzard.hub.api.auth_session import TENANT_HEADER
from blizzard.hub.auth.invitations import InvitationToken
from blizzard.hub.store import schema
from tests.support import (
    HubHarness,
    build_hub,
    default_tenant,
    hub_scoped,
    seed_hub_admin,
    seed_invitation,
    seed_session,
    seed_user,
)

pytestmark = pytest.mark.component


def _cookie(token: str) -> dict[str, str]:
    return {"Cookie": f"bz_session={token}"}


def _admin(hub: HubHarness) -> dict[str, str]:
    return _cookie(seed_session(hub, seed_user(hub, username="tenant-admin", role=Role.ADMIN)))


def _invite(hub: HubHarness, headers: dict[str, str], **body: Any) -> Any:
    return hub.client.post(
        "/api/invitations", json={"email": "new@example.com", "role": "guest", **body}, headers=headers
    )


def _stored(hub: HubHarness) -> list[sa.Row[Any]]:
    with hub.engine.connect() as conn:
        return list(conn.execute(hub_scoped(sa.select(schema.invitations))).all())


def test_an_admin_invites_and_only_the_issuing_answer_carries_the_token(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = _admin(hub)

    issued = _invite(hub, admin, email="New.Person@Example.COM", role="admin")

    assert issued.status_code == 201, issued.text
    body = issued.json()
    view = body["invitation"]
    assert view["email"] == "new.person@example.com"
    assert view["role"] == "admin"
    assert view["state"] == "live"
    assert view["accepted_by"] is None
    created_at = datetime.fromisoformat(view["created_at"])
    assert datetime.fromisoformat(view["expires_at"]) - created_at == timedelta(days=7)
    [row] = _stored(hub)
    assert row.tenant_id == default_tenant(hub.engine)
    assert row.email == "new.person@example.com"
    assert row.token_hash == InvitationToken(body["token"]).hash
    assert body["token"] not in {str(value) for value in row}

    listed = hub.client.get("/api/invitations", headers=admin)

    assert listed.status_code == 200, listed.text
    assert listed.json() == {"invitations": [view]}
    assert body["token"] not in listed.text


@pytest.mark.parametrize("expires_in", [0, 31, 7.5, "7", None])
def test_a_lifetime_that_is_not_1_to_30_whole_days_is_refused(tmp_path: Path, expires_in: object) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")

    refused = _invite(hub, _admin(hub), expires_in=expires_in)

    assert refused.status_code == 422, refused.text
    assert _stored(hub) == []


def test_an_invitation_may_live_as_long_as_30_days(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")

    view = _invite(hub, _admin(hub), expires_in=30).json()["invitation"]

    assert datetime.fromisoformat(view["expires_at"]) - datetime.fromisoformat(view["created_at"]) == timedelta(days=30)


@pytest.mark.parametrize("role", ["pending", "superuser"])
def test_a_role_no_membership_holds_is_refused(tmp_path: Path, role: str) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")

    refused = _invite(hub, _admin(hub), role=role)

    assert refused.status_code == 422, refused.text
    assert refused.json() == {"detail": f"{role} is not a membership role"}
    assert _stored(hub) == []


def test_a_role_the_hub_does_not_know_is_refused(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")

    refused = _invite(hub, _admin(hub), role="owner")

    assert refused.status_code == 422, refused.text
    assert _stored(hub) == []


def test_the_answer_never_tells_whether_the_address_already_has_an_account(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = _admin(hub)
    seed_user(hub, username="known", role=Role.GUEST, email="known@example.com")

    known = _invite(hub, admin, email="known@example.com").json()
    unknown = _invite(hub, admin, email="unknown@example.com").json()

    def _shape(body: dict[str, Any]) -> dict[str, Any]:
        view = dict(body["invitation"])
        for key in ("invitation_id", "email"):
            view.pop(key)
        return {"keys": sorted(body), "invitation": view}

    assert _shape(known) == _shape(unknown)


def test_revoking_ends_a_live_invitation_once(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = _admin(hub)
    invitation_id = _invite(hub, admin).json()["invitation"]["invitation_id"]

    revoked = hub.client.post(f"/api/invitations/{invitation_id}/revocations", headers=admin)

    assert revoked.status_code == 201, revoked.text
    assert revoked.json()["state"] == "revoked"
    assert revoked.json()["accepted_by"] is None
    assert [v["state"] for v in hub.client.get("/api/invitations", headers=admin).json()["invitations"]] == ["revoked"]
    again = hub.client.post(f"/api/invitations/{invitation_id}/revocations", headers=admin)
    assert again.status_code == 409, again.text
    unknown = hub.client.post("/api/invitations/inv_unknown/revocations", headers=admin)
    assert unknown.status_code == 404, unknown.text


def test_an_expired_invitation_lists_as_expired(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    seed_invitation(hub.engine, default_tenant(hub.engine), "old@example.com", Role.GUEST, lifetime=timedelta(days=1))
    hub.clock.advance(timedelta(days=2))
    admin = _admin(hub)

    [view] = hub.client.get("/api/invitations", headers=admin).json()["invitations"]

    assert view["state"] == "expired"


def test_a_hub_administrator_without_a_membership_manages_any_tenants_invitations(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    hub_admin = seed_user(hub, username="hub-admin", role=Role.PENDING)
    seed_hub_admin(hub.engine, hub_admin.user_id)
    other = hub.add_tenant("other")
    headers = {**_cookie(seed_session(hub, hub_admin)), TENANT_HEADER: other.tenant}

    issued = _invite(hub, headers, role="admin")

    assert issued.status_code == 201, issued.text
    invitation_id = issued.json()["invitation"]["invitation_id"]
    assert issued.json()["invitation"]["created_by"] == hub_admin.user_id
    assert [row.tenant_id for row in _stored(hub)] == [other.tenant]
    listed = hub.client.get("/api/invitations", headers=headers)
    assert [v["invitation_id"] for v in listed.json()["invitations"]] == [invitation_id]
    revoked = hub.client.post(f"/api/invitations/{invitation_id}/revocations", headers=headers)
    assert revoked.status_code == 201, revoked.text
    # The admission reaches the invitations alone: the tenant's other routes still turn a non-member away.
    assert hub.client.get("/api/runners", headers=headers).status_code == 404


def test_a_tenant_admin_never_reaches_another_tenants_invitations(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = _admin(hub)
    other = hub.add_tenant("other")
    foreign_id, _token = seed_invitation(hub.engine, other.tenant, "far@example.com", Role.GUEST)

    assert hub.client.get("/api/invitations", headers=admin).json() == {"invitations": []}
    revoke = hub.client.post(f"/api/invitations/{foreign_id}/revocations", headers=admin)
    assert revoke.status_code == 404, revoke.text
    named = hub.client.get("/api/invitations", headers={**admin, TENANT_HEADER: other.tenant})
    assert named.status_code == 404, named.text
