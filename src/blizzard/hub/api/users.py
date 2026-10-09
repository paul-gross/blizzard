"""``GET /api/users`` / ``POST /api/users/{user_id}/role`` — the user listing and
role-assignment API, gated on ``user:manage``, human-plane throughout.

Both act on memberships in the actor's tenant: a user's role is their membership's role
there, ``pending`` with none, and ``superuser`` for a hub administrator. Role-change rules
live in ``AuthService.assign_role`` (``bzh:controller-read-only``); this module resolves
identities, maps ``RoleAssignmentRefused`` to ``403``, and renders.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from blizzard.auth_core import Permission, Role
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.hub_level import Hub
from blizzard.hub.auth.models import Identity, ResolvedIdentity, wire_role
from blizzard.hub.auth.service import Member, RoleAssignmentRefused, assignable_roles
from blizzard.hub.tenancy import TenantId
from blizzard.wire.user import RoleAssignmentRequest, UserIdentityView, UserView

router = APIRouter(prefix="/api", tags=["auth"], dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)])


def _user_view(member: Member, *, identities: list[Identity], actor: ResolvedIdentity) -> UserView:
    user = member.user
    return UserView(
        user_id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role=wire_role(member.role, hub_admin=member.hub_admin),
        created_at=iso_utc(user.created_at),
        identities=[UserIdentityView(provider_name=i.provider_name, handle=i.handle) for i in identities],
        assignable_roles=assignable_roles(actor=actor, subject=member),
    )


def _tenant_of(actor: ResolvedIdentity) -> TenantId:
    assert actor.tenant is not None, "a tenant route resolves its principal for the request's tenant"
    return actor.tenant


@router.get("/users", response_model=list[UserView])
def list_users(
    services: Hub,
    actor: Annotated[ResolvedIdentity, Depends(require(Permission.USER_MANAGE))],
) -> list[UserView]:
    """Every hub-local account — the admin page's own table."""
    users = services.users.list_all()
    identities_by_user = services.identities.list_for_users([u.user_id for u in users])
    members = services.auth.members(users, tenant=_tenant_of(actor))
    return [_user_view(m, identities=identities_by_user.get(m.user.user_id, []), actor=actor) for m in members]


@router.post("/users/{user_id}/role", response_model=UserView)
def assign_role(
    user_id: str,
    body: RoleAssignmentRequest,
    services: Hub,
    actor: Annotated[ResolvedIdentity, Depends(require(Permission.USER_MANAGE))],
) -> UserView:
    """Assign ``user_id`` a new role in the actor's tenant — ``pending`` revokes their
    membership. Three refusals, each a 403: changing your own role; any change touching
    ``superuser`` (a hub administrator); and granting or revoking ``admin`` as anyone but a
    hub administrator. A no-op change is accepted and records
    nothing. A real one records a ``user_role_changed`` fact and takes effect on the
    subject's next request, with no re-login."""
    try:
        to_role = Role(body.role)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"unknown role {body.role!r}") from exc
    subject = services.users.get(user_id)
    if subject is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown user {user_id!r}")
    try:
        updated = services.auth.assign_role(
            actor=actor, subject=services.auth.member(subject, tenant=_tenant_of(actor)), to_role=to_role
        )
    except RoleAssignmentRefused as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _user_view(updated, identities=services.identities.list_for_user(user_id), actor=actor)
