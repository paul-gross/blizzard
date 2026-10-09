"""``GET /api/users`` / ``POST /api/users/{user_id}/role`` — deprecated aliases of ``GET /api/members`` and
``PUT``/``DELETE /api/members/{user_id}`` (``src/blizzard/hub/api/members.py``), acting in the request's tenant.

They keep their former shape: a member's ``role`` reads ``superuser`` for a hub administrator, and assigning ``pending``
revokes the membership. The listing holds the tenant's members alone, and a user id that is no member answers ``404``
exactly as one that does not exist. Gated on ``user:manage`` in the tenant; the membership rules and their answers are
the members routes' own.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from blizzard.auth_core import Permission, Role
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.hub_level import Hub
from blizzard.hub.api.members import change_membership, identity_views, tenant_of
from blizzard.hub.auth.memberships import Member, assignable_roles, revocable
from blizzard.hub.auth.models import Identity, ResolvedIdentity, wire_role
from blizzard.wire.user import RoleAssignmentRequest, UserView

router = APIRouter(
    prefix="/api",
    tags=["auth"],
    deprecated=True,
    dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)],
)

_Manager = Annotated[ResolvedIdentity, Depends(require(Permission.USER_MANAGE))]


def _user_view(member: Member, *, identities: list[Identity], actor: ResolvedIdentity, admins: int) -> UserView:
    user = member.user
    revoke = [Role.PENDING] if revocable(actor=actor, subject=member, admins=admins) else []
    return UserView(
        user_id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role=wire_role(member.role, hub_admin=member.hub_admin),
        created_at=iso_utc(user.created_at),
        identities=identity_views(identities),
        assignable_roles=revoke + assignable_roles(actor=actor, subject=member, admins=admins),
    )


@router.get("/users", response_model=list[UserView])
def list_users(services: Hub, actor: _Manager) -> list[UserView]:
    """Every member of the request's tenant — :func:`~blizzard.hub.api.members.list_members` in the former shape."""
    members = services.members.listing(tenant_of(actor))
    identities = services.identities.list_for_users([m.user.user_id for m in members])
    admins = sum(1 for m in members if m.role is Role.ADMIN)
    return [_user_view(m, identities=identities.get(m.user.user_id, []), actor=actor, admins=admins) for m in members]


@router.post("/users/{user_id}/role", response_model=UserView)
def assign_role(user_id: str, body: RoleAssignmentRequest, services: Hub, actor: _Manager) -> UserView:
    """Give ``user_id`` ``role`` in the request's tenant — ``pending`` revokes the membership — under the members
    routes' rules and answers. ``superuser`` names the hub administrator role, which no membership change grants
    (``403``); an unknown role string answers ``400``."""
    try:
        to_role = Role(body.role)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"unknown role {body.role!r}") from exc
    if to_role is Role.SUPERUSER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="superuser is not assignable through the API")
    member = change_membership(services, actor, user_id, None if to_role is Role.PENDING else to_role)
    return _user_view(
        member,
        identities=services.identities.list_for_user(user_id),
        actor=actor,
        admins=services.members.admins(member.tenant),
    )
