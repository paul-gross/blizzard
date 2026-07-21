"""``GET /api/users`` / ``POST /api/users/{user_id}/role`` — the admin page's user
listing and role-assignment API (issue #94), gated on ``user:manage``.

Both routes are human-plane (``dependencies=[Depends(reject_runner_principal)]`` at
router level, mirroring every other operator router — a runner's bearer token is
confined to the fleet plane). Role-change rules (self-change, ``superuser`` grant/
revoke, ``superuser`` not assignable) live in ``AuthService.assign_role``
(``bzh:controller-read-only`` — this controller stays read-only and delegates the
write); this module only resolves the acting identity and the subject row, translates
:class:`~blizzard.hub.auth.service.RoleAssignmentRefused` to ``403``, and renders the
result.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from blizzard.auth_core import USER_MANAGE, Role
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.auth.models import ResolvedIdentity, User
from blizzard.hub.auth.service import RoleAssignmentRefused
from blizzard.hub.composition import HubServices
from blizzard.wire.user import RoleAssignmentRequest, UserIdentityView, UserView

router = APIRouter(prefix="/api", tags=["auth"], dependencies=[Depends(reject_runner_principal)])


def _user_view(user: User, *, services: HubServices) -> UserView:
    identities = services.identities.list_for_user(user.user_id)
    return UserView(
        user_id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role=user.role.value,
        created_at=iso_utc(user.created_at),
        identities=[UserIdentityView(provider_name=i.provider_name, handle=i.handle) for i in identities],
    )


@router.get("/users", response_model=list[UserView], dependencies=[Depends(require(USER_MANAGE))])
def list_users(services: Annotated[HubServices, Depends(get_services)]) -> list[UserView]:
    """Every hub-local account — the admin page's own table."""
    return [_user_view(u, services=services) for u in services.users.list_all()]


@router.post("/users/{user_id}/role", response_model=UserView)
def assign_role(
    user_id: str,
    body: RoleAssignmentRequest,
    services: Annotated[HubServices, Depends(get_services)],
    actor: Annotated[ResolvedIdentity, Depends(require(USER_MANAGE))],
) -> UserView:
    """Assign ``user_id`` a new role; ``AuthService.assign_role`` enforces every rule
    (self-change, ``superuser`` grant/revoke, ``superuser`` not assignable — see its own
    docstring) and records the ``user_role_changed`` fact on a real change. Takes effect
    on the subject's next request with no re-login (``resolve_identity`` reads
    ``users.role`` live on every resolve, issue #91)."""
    try:
        to_role = Role(body.role)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"unknown role {body.role!r}") from exc
    subject = services.users.get(user_id)
    if subject is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown user {user_id!r}")
    try:
        updated = services.auth.assign_role(actor=actor, subject=subject, to_role=to_role)
    except RoleAssignmentRefused as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _user_view(updated, services=services)
