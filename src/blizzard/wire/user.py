"""Admin-page wire bodies — the user listing and role-assignment API (issue #94).

``GET /api/users`` lists every hub-local account (username, display name, email,
linked identities, role, created); ``POST /api/users/{id}/role`` assigns a role
(``hub/api/users.py``).
"""

from __future__ import annotations

from pydantic import BaseModel


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
    role: str
    created_at: str
    identities: list[UserIdentityView] = []


class RoleAssignmentRequest(BaseModel):
    """``POST /api/users/{id}/role`` body — the target role, by its ``auth_core.Role``
    value."""

    role: str
