"""``GET /api/admin/hub-admins`` and ``PUT``/``DELETE /api/admin/hub-admins/{user_id}`` — the hub administrators.

Hub-level: these routes serve no tenant and take ``hub-admin:grant``, which only a hub administrator holds, so no
membership role — a tenant's admin included — reaches them. Revoking the last hub administrator answers ``409``. The
rules live in :class:`~blizzard.hub.auth.hub_admins.HubAdministrators` (``bzh:controller-read-only``)."""

from __future__ import annotations

from functools import partial
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import APIRouter, Depends, Response, status

from blizzard.auth_core import Permission
from blizzard.hub.api import problems_admin
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require_hub
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.auth.hub_admins import HubAdministrators, LastHubAdminKept
from blizzard.hub.auth.models import ResolvedIdentity, User
from blizzard.hub.auth.users import IReadUserRepository
from blizzard.wire.user import HubAdminView

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(reject_runner_principal)])

_Granter = Annotated[ResolvedIdentity, Depends(require_hub(Permission.HUB_ADMIN_GRANT))]


def _view(user: User) -> HubAdminView:
    return HubAdminView(user_id=user.user_id, username=user.username, display_name=user.display_name, email=user.email)


def _subject(users: IReadUserRepository, user_id: str) -> User:
    user = users.get(user_id)
    if user is None:
        raise ProblemHTTPException(partial(problems_admin.user_not_found_problem, "no such user"))
    return user


@router.get("/hub-admins", response_model=list[HubAdminView])
@inject_sync
def list_hub_admins(admins: FromDishka[HubAdministrators], actor: _Granter) -> list[HubAdminView]:
    """Every hub administrator."""
    del actor
    return [_view(u) for u in admins.listing()]


@router.put("/hub-admins/{user_id}", response_model=HubAdminView)
@inject_sync
def grant_hub_admin(
    user_id: str, admins: FromDishka[HubAdministrators], users: FromDishka[IReadUserRepository], actor: _Granter
) -> HubAdminView:
    """Make ``user_id`` a hub administrator; granting a holder writes nothing."""
    subject = _subject(users, user_id)
    admins.grant(subject, by=actor.user_id)
    return _view(subject)


@router.delete("/hub-admins/{user_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
@inject_sync
def revoke_hub_admin(
    user_id: str, admins: FromDishka[HubAdministrators], users: FromDishka[IReadUserRepository], actor: _Granter
) -> Response:
    """Revoke ``user_id``'s hub administrator role; revoking one who holds none writes nothing, and revoking the last
    one answers ``409``."""
    try:
        admins.revoke(_subject(users, user_id), by=actor.user_id)
    except LastHubAdminKept as exc:
        raise ProblemHTTPException(partial(problems_admin.last_hub_admin_problem, str(exc))) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
