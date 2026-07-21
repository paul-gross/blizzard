"""Session-cookie/bearer resolution + the ``require(<permission>)`` route dependency —
the human-plane edge seam (issue #91).

Mirrors ``hub/api/auth.py``'s runner-bearer-token seam in shape: a presented
credential is hashed and resolved via the **read** repository
(``services.sessions.get_by_hash``, the same read-at-the-edge pattern
``require_runner_principal`` uses over ``registration_for_token_hash``), and the
sliding-expiry write is delegated to the domain (``services.auth.touch_session`` —
``bzh:controller-read-only``: no mutation happens here). Unlike the runner seam there
is no ``warn``/``enforce`` rollout brake — human auth is gated outright once
``auth.mode = "oauth"`` is configured; ``mode = "none"`` (the default) is the rollout
brake, short-circuiting every resolution to the implicit operator/superuser identity
with **no store read at all**, so the store-free export/unit app and every existing
test stay unaffected.

``require(permission)`` deliberately does **not** declare ``Depends(get_services)`` as
a parameter — it checks ``auth.mode`` first and only reaches for services under
``oauth``, so a route gated under the default ``none`` mode never 503s on an unwired
store (the SSE stream, ``hub/api/events.py``, is dependency-free on the store-free app
and must stay that way).
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException, Request, status

from blizzard.auth_core import Permission, Role, expand
from blizzard.hub.api.deps import get_services
from blizzard.hub.auth.hashing import hash_session_id
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import HubServices
from blizzard.hub.config import AUTH_MODE_NONE

_SESSION_COOKIE_NAME = "bz_session"
_BEARER_PREFIX = "Bearer "

#: The implicit identity every request resolves to under ``auth.mode = "none"`` — the
#: unauthenticated ``"operator"`` singleton every existing route/test already assumes,
#: now expressed as a full :class:`ResolvedIdentity` carrying every permission
#: (``superuser``).
IMPLICIT_OPERATOR = ResolvedIdentity(
    user_id="operator",
    username="operator",
    display_name="operator",
    role=Role.SUPERUSER,
    permissions=expand(Role.SUPERUSER),
)


def _presented_session_id(request: Request) -> str | None:
    """The session id from the ``HttpOnly`` cookie or an ``Authorization: Bearer``
    header (the CLI path, #96) — the cookie wins when both are present."""
    cookie = request.cookies.get(_SESSION_COOKIE_NAME)
    if cookie:
        return cookie
    header = request.headers.get("authorization", "")
    if header.startswith(_BEARER_PREFIX):
        return header[len(_BEARER_PREFIX) :]
    return None


def resolve_identity(request: Request, services: HubServices | None) -> ResolvedIdentity | None:
    """Resolve the presented session to a :class:`ResolvedIdentity`, or ``None`` when
    no session is presented, it does not resolve, or it has expired.

    Under ``auth.mode = "none"`` this never touches ``services`` (``None`` is a legal
    argument in that case — the store-free export/unit app calls this with none wired)
    — it always answers :data:`IMPLICIT_OPERATOR`. Under ``oauth`` it looks up the
    session by its stored hash via the **read** session repository, then delegates the
    sliding-expiry write and role expansion to the domain
    (``services.auth.touch_session`` — ``bzh:domain-takes-objects``: the loaded
    ``Session`` is handed in, never an id)."""
    mode = request.app.state.config.auth.mode
    if mode == AUTH_MODE_NONE:
        return IMPLICIT_OPERATOR
    assert services is not None  # `require`/`me` already resolved services under oauth
    session_id = _presented_session_id(request)
    if session_id is None:
        return None
    session = services.sessions.get_by_hash(hash_session_id(session_id))
    if session is None:
        return None
    return services.auth.touch_session(session)


def resolved_username(request: Request) -> str:
    """The attribution sites' (``questions.py``, ``decisions.py``) resolved identity
    username — ``"operator"`` under ``none``, or the resolved session's username under
    ``oauth``. Reads ``request.app.state`` directly (rather than taking a
    ``Depends(get_services)`` parameter) because this is a plain helper a route body
    calls after its own ``require(<permission>)`` dependency has already run — by then,
    under ``oauth``, services are guaranteed wired (``require`` would already have
    503'd otherwise)."""
    mode = request.app.state.config.auth.mode
    if mode == AUTH_MODE_NONE:
        return IMPLICIT_OPERATOR.username
    services = get_services(request)
    identity = resolve_identity(request, services)
    return identity.username if identity is not None else IMPLICIT_OPERATOR.username


def require(permission: Permission) -> Callable[[Request], ResolvedIdentity]:
    """A dependency factory: the returned dependency resolves the identity and raises
    ``401`` (no/expired session) or ``403`` (insufficient permission); grants under
    ``auth.mode = "none"`` unconditionally. Each human route declares the one
    permission it needs — reshaping a role touches only ``ROLE_PERMISSIONS``
    (``blizzard.auth_core``), never a call site."""

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
