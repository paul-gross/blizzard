"""``GET /api/me`` — the resolved identity and its expanded permission set (issue #91).

Public plane (no ``require(<permission>)``): a ``guest`` must reach this route to
discover it has no permissions, so gating it on a permission would be
self-defeating. It is the one route that distinguishes *anonymous* (no/expired
session — 401) from *denied* (a resolved identity lacking a permission — every other
route's 403) by calling :func:`~blizzard.hub.api.auth_session.resolve_identity`
directly rather than going through ``require()``.

Permissions are computed server-side here and nowhere else on the client — the web
frontend (#93) reads this response's ``permissions`` list rather than re-typing
``ROLE_PERMISSIONS``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from blizzard.hub.api.auth_session import resolve_identity
from blizzard.hub.api.deps import get_services
from blizzard.hub.config import AUTH_MODE_NONE

router = APIRouter(prefix="/api", tags=["auth"])


class MeResponse(BaseModel):
    """The resolved identity's wire view — the board's own-identity read."""

    user_id: str
    username: str
    display_name: str
    role: str
    permissions: list[str]


@router.get("/me", response_model=MeResponse)
def me(request: Request) -> MeResponse:
    """Under ``auth.mode = "none"`` the implicit operator/superuser; under ``oauth``
    with a resolving session, the session's identity; under ``oauth`` with none, 401."""
    mode = request.app.state.config.auth.mode
    services = None if mode == AUTH_MODE_NONE else get_services(request)
    identity = resolve_identity(request, services)
    if identity is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    return MeResponse(
        user_id=identity.user_id,
        username=identity.username,
        display_name=identity.display_name,
        role=identity.role.value,
        permissions=sorted(identity.permissions),
    )
