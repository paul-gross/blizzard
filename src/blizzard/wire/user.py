"""People wire bodies — a tenant's members, the hub administrators, and the deprecated user routes.

``GET /api/members`` lists the request tenant's members and ``PUT``/``DELETE /api/members/{user_id}`` grant, change and
revoke one (``src/blizzard/hub/api/members.py``); ``GET``/``PUT``/``DELETE /api/admin/hub-admins[/{user_id}]`` list,
grant and revoke the hub administrator role (``src/blizzard/hub/api/hub_admins.py``). ``GET /api/users`` and
``POST /api/users/{id}/role`` are their deprecated aliases (``src/blizzard/hub/api/users.py``).
"""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.auth_core import Role


class UserIdentityView(BaseModel):
    """One linked provider identity."""

    provider_name: str
    handle: str


class MemberView(BaseModel):
    """One member of the request's tenant."""

    user_id: str
    username: str
    display_name: str
    email: str | None
    #: The membership's role in the tenant — ``guest``, ``contributor`` or ``admin``.
    role: Role
    #: Whether the member also holds the hub administrator role, which grants nothing inside the tenant by itself.
    hub_admin: bool
    created_at: str
    identities: list[UserIdentityView] = []
    #: The membership roles the requesting actor may give this member — empty when the rules admit no change.
    assignable_roles: list[Role] = []
    #: Whether the requesting actor may revoke this membership.
    revocable: bool = False


class MemberRoleRequest(BaseModel):
    """``PUT /api/members/{user_id}`` body — the membership role to grant: ``guest``, ``contributor`` or ``admin``."""

    role: Role


class HubAdminView(BaseModel):
    """One hub administrator."""

    user_id: str
    username: str
    display_name: str
    email: str | None


class UserView(BaseModel):
    """One member as the deprecated ``/api/users`` routes render them — ``role`` is ``superuser`` for a hub
    administrator, else the membership's role, ``pending`` once revoked."""

    user_id: str
    username: str
    display_name: str
    email: str | None
    role: Role
    created_at: str
    identities: list[UserIdentityView] = []
    #: The roles the requesting actor may assign this user — ``pending`` revokes; empty when the rules admit none.
    assignable_roles: list[Role] = []


class RoleAssignmentRequest(BaseModel):
    """``POST /api/users/{id}/role`` body — the target role, by its wire value; ``pending`` revokes."""

    role: str
