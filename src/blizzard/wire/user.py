"""Admin-page wire bodies — the user listing and role-assignment API.

``GET /api/users`` lists every hub-local account (username, display name, email,
linked identities, role, created); ``POST /api/users/{id}/role`` assigns a role
(``src/blizzard/hub/api/users.py``).
"""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.auth_core import Role


class UserIdentityView(BaseModel):
    """One linked provider identity."""

    provider_name: str
    handle: str


class UserView(BaseModel):
    """One ``users`` row — the listing/assignment response shape."""

    user_id: str
    username: str
    display_name: str
    email: str | None
    role: Role
    created_at: str
    identities: list[UserIdentityView] = []
    #: The roles the requesting actor may assign this user — empty when the role-change rules admit none.
    assignable_roles: list[Role] = []


class RoleAssignmentRequest(BaseModel):
    """``POST /api/users/{id}/role`` body — the target role, by its wire value."""

    role: str
