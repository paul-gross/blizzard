"""``GET /api/members`` and ``PUT``/``DELETE /api/members/{user_id}`` — a tenant's members and their memberships,
human-plane throughout and acting in the request's tenant.

Listing takes ``user:manage`` in the tenant. Granting, changing and revoking take ``user:manage`` there, or
``membership:grant-any``: their router admits a hub administrator into a tenant they hold no membership in under that
permission alone, and a tenant that does not exist or is closed answers as on any tenant route. A member is named by
user id; a user id that is no member answers ``404`` exactly as one that does not exist, unless the actor may grant
anyone. The rules live in :class:`~blizzard.hub.auth.memberships.TenantMembers` (``bzh:controller-read-only``); this
module resolves the subject, maps each refusal to its answer, and renders.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from blizzard.auth_core import Permission, Role
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import TenantBoundAdmitting, require, require_either, tenant_bound
from blizzard.hub.api.hub_level import Hub
from blizzard.hub.auth.memberships import (
    MEMBERSHIP_ROLES,
    LastAdminKept,
    Member,
    MembershipChangeRefused,
    NotAMember,
    assignable_roles,
    revocable,
)
from blizzard.hub.auth.models import Identity, ResolvedIdentity
from blizzard.hub.composition import HubServices
from blizzard.hub.tenancy import StoreScope, TenantId
from blizzard.wire.user import MemberRoleRequest, MemberView, UserIdentityView

router = APIRouter(
    prefix="/api", tags=["members"], dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)]
)
#: The membership changes, which a hub administrator may make in any open tenant without a membership of their own.
grants_router = APIRouter(
    prefix="/api",
    tags=["members"],
    dependencies=[
        Depends(reject_runner_principal),
        Depends(TenantBoundAdmitting(Permission.MEMBERSHIP_GRANT_ANY)),
    ],
)

_Granter = Annotated[ResolvedIdentity, Depends(require_either(Permission.USER_MANAGE, Permission.MEMBERSHIP_GRANT_ANY))]


def tenant_of(actor: ResolvedIdentity) -> TenantId:
    """The tenant a tenant route's principal was resolved for."""
    assert actor.tenant is not None, "a tenant route resolves its principal for the request's tenant"
    return actor.tenant


def identity_views(identities: list[Identity]) -> list[UserIdentityView]:
    return [UserIdentityView(provider_name=i.provider_name, handle=i.handle) for i in identities]


def change_membership(services: HubServices, actor: ResolvedIdentity, user_id: str, to_role: Role | None) -> Member:
    """Move ``user_id`` to ``to_role`` (``None`` revokes) in the actor's tenant, answering each refusal: ``404`` for a
    user that does not exist or is no member, alike; ``403`` for changing your own role; ``409`` for the tenant's last
    admin. A revocation ends the user's open event streams in the tenant."""
    subject = services.users.get(user_id)
    if subject is None:
        raise _unknown(NotAMember(user_id))
    try:
        member = services.members.change(
            actor=actor, subject=services.members.member(subject, tenant=tenant_of(actor)), to_role=to_role
        )
    except NotAMember as exc:
        raise _unknown(exc) from exc
    except MembershipChangeRefused as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LastAdminKept as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if member.role is None:
        # The revocation has committed: the user's open event streams in the tenant end. Every membership role
        # holds the stream's permission, so a role change ends none.
        services.tenants.open_services(StoreScope(member.tenant)).events.end_subscriber(user_id)
    return member


def _unknown(exc: NotAMember) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _member_view(member: Member, *, identities: list[Identity], actor: ResolvedIdentity, admins: int) -> MemberView:
    assert member.role is not None, "a listed or granted member holds a membership"
    user = member.user
    return MemberView(
        user_id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role=member.role,
        hub_admin=member.hub_admin,
        created_at=iso_utc(user.created_at),
        identities=identity_views(identities),
        assignable_roles=assignable_roles(actor=actor, subject=member, admins=admins),
        revocable=revocable(actor=actor, subject=member, admins=admins),
    )


@router.get("/members", response_model=list[MemberView])
def list_members(
    services: Hub,
    actor: Annotated[ResolvedIdentity, Depends(require(Permission.USER_MANAGE))],
) -> list[MemberView]:
    """Every member of the request's tenant, with the changes the actor may make to each."""
    members = services.members.listing(tenant_of(actor))
    identities = services.identities.list_for_users([m.user.user_id for m in members])
    admins = sum(1 for m in members if m.role is Role.ADMIN)
    return [_member_view(m, identities=identities.get(m.user.user_id, []), actor=actor, admins=admins) for m in members]


@grants_router.put("/members/{user_id}", response_model=MemberView)
def grant_membership(user_id: str, body: MemberRoleRequest, services: Hub, actor: _Granter) -> MemberView:
    """Grant ``user_id`` ``role`` in the request's tenant, or change the role they hold there. A role equal to the one
    in force writes nothing. Refused: a role that is no membership role (``422``), a user that does not exist or —
    without ``membership:grant-any`` — is no member (``404``, alike), your own role (``403``), and demoting the
    tenant's last admin (``409``)."""
    if body.role not in MEMBERSHIP_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"{body.role.value} is not a membership role"
        )
    member = change_membership(services, actor, user_id, body.role)
    return _member_view(
        member,
        identities=services.identities.list_for_user(user_id),
        actor=actor,
        admins=services.members.admins(member.tenant),
    )


@grants_router.delete("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_membership(user_id: str, services: Hub, actor: _Granter) -> Response:
    """Revoke ``user_id``'s membership in the request's tenant. Refused as a grant is: a user that is no member
    (``404``, unless the actor holds ``membership:grant-any``, for whom it writes nothing), your own (``403``), and the
    tenant's last admin (``409``)."""
    change_membership(services, actor, user_id, None)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
