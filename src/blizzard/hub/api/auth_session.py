"""Session-cookie/bearer resolution + the ``require(<permission>)`` route dependency —
the human-plane edge seam (issue #91).

A presented credential is hashed and resolved via the **read** repository; the
sliding-expiry write is delegated to the domain (``bzh:controller-read-only``).
``auth.mode = "none"`` (the default) short-circuits to the implicit operator identity."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from blizzard.auth_core import Permission, Role, expand
from blizzard.hub.api.bearer import presented_bearer
from blizzard.hub.api.deps import get_services
from blizzard.hub.auth.hashing import SessionId
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import HubServices
from blizzard.hub.config import AUTH_MODE_NONE

_SESSION_COOKIE_NAME = "bz_session"

#: The implicit identity every request resolves to under ``auth.mode = "none"`` — the
#: unauthenticated ``"operator"`` singleton, carrying every permission (``superuser``).
IMPLICIT_OPERATOR = ResolvedIdentity(
    user_id="operator",
    username="operator",
    display_name="operator",
    role=Role.SUPERUSER,
    permissions=expand(Role.SUPERUSER),
)


@dataclass(frozen=True)
class PresentedSession:
    """The session credential one request carries: the ``HttpOnly`` cookie or an
    ``Authorization: Bearer`` header (the CLI path, #96) — the cookie wins."""

    request: Request

    @property
    def id_hash(self) -> str | None:
        """The digest to look the session up by, or ``None`` when none was presented."""
        session_id = self.request.cookies.get(_SESSION_COOKIE_NAME) or presented_bearer(self.request)
        return None if session_id is None else SessionId(session_id).hash


def resolve_identity(request: Request, services: HubServices | None) -> ResolvedIdentity | None:
    """Resolve the presented session to a :class:`ResolvedIdentity`, or ``None`` when
    no session is presented, it does not resolve, or it has expired.

    Under ``auth.mode = "none"`` this never touches ``services`` (``None`` is legal
    then) and always answers :data:`IMPLICIT_OPERATOR`."""
    mode = request.app.state.config.auth.mode
    if mode == AUTH_MODE_NONE:
        return IMPLICIT_OPERATOR
    assert services is not None  # `require`/`me` already resolved services under oauth
    id_hash = PresentedSession(request).id_hash
    if id_hash is None:
        return None
    session = services.sessions.get_by_hash(id_hash)
    if session is None:
        return None
    return services.auth.touch_session(session)


def resolved_username(request: Request) -> str:
    """The resolved identity's username — ``"operator"`` under ``none``, the session's
    username under ``oauth``.

    Reads ``request.app.state`` directly: a plain helper a route body calls once its own
    ``require(<permission>)`` dependency has already guaranteed services are wired."""
    mode = request.app.state.config.auth.mode
    if mode == AUTH_MODE_NONE:
        return IMPLICIT_OPERATOR.username
    services = get_services(request)
    identity = resolve_identity(request, services)
    return identity.username if identity is not None else IMPLICIT_OPERATOR.username


def require(permission: Permission) -> Callable[[Request], ResolvedIdentity]:
    """A dependency factory: the returned dependency resolves the identity and raises
    ``401`` (no/expired session) or ``403`` (insufficient permission); grants under
    ``auth.mode = "none"`` unconditionally, without reaching for services."""

    def _dependency(request: Request) -> ResolvedIdentity:
        mode = request.app.state.config.auth.mode
        if mode == AUTH_MODE_NONE:
            return IMPLICIT_OPERATOR
        services = get_services(request)
        identity = resolve_identity(request, services)
        if identity is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")
        if permission not in identity.permissions:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"missing permission {permission!r}")
        return identity

    return _dependency
