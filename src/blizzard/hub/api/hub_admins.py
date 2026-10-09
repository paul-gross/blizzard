"""``GET /api/admin/hub-admins`` and ``PUT``/``DELETE /api/admin/hub-admins/{user_id}`` — the hub administrators.

Hub-level: these routes serve no tenant and take ``hub-admin:grant``, which only a hub administrator holds, so no
membership role — a tenant's admin included — reaches them. Revoking the last hub administrator answers ``409``. The
rules live in :class:`~blizzard.hub.auth.hub_admins.HubAdministrators` (``bzh:controller-read-only``)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from blizzard.auth_core import Permission
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require_hub
from blizzard.hub.api.hub_level import Hub
from blizzard.hub.auth.hub_admins import LastHubAdminKept
from blizzard.hub.auth.models import ResolvedIdentity, User
from blizzard.hub.composition import HubServices
from blizzard.wire.user import HubAdminView

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(reject_runner_principal)])

_Granter = Annotated[ResolvedIdentity, Depends(require_hub(Permission.HUB_ADMIN_GRANT))]


def _view(user: User) -> HubAdminView:
    return HubAdminView(user_id=user.user_id, username=user.username, display_name=user.display_name, email=user.email)


def _subject(services: HubServices, user_id: str) -> User:
    user = services.users.get(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such user")
    return user


@router.get("/hub-admins", response_model=list[HubAdminView])
def list_hub_admins(services: Hub, actor: _Granter) -> list[HubAdminView]:
    """Every hub administrator."""
    del actor
    return [_view(u) for u in services.hub_admins.listing()]


@router.put("/hub-admins/{user_id}", response_model=HubAdminView)
def grant_hub_admin(user_id: str, services: Hub, actor: _Granter) -> HubAdminView:
    """Make ``user_id`` a hub administrator; granting a holder writes nothing."""
    subject = _subject(services, user_id)
    services.hub_admins.grant(subject, by=actor.user_id)
    return _view(subject)


@router.delete("/hub-admins/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_hub_admin(user_id: str, services: Hub, actor: _Granter) -> Response:
    """Revoke ``user_id``'s hub administrator role; revoking one who holds none writes nothing, and revoking the last
    one answers ``409``."""
    try:
        services.hub_admins.revoke(_subject(services, user_id), by=actor.user_id)
    except LastHubAdminKept as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
