"""The runner's SSO federation bounce, and the gates the human web lane depends on (issue #95).

``login`` stashes a random ``state`` and ``return_to`` in two short-lived cookies and redirects to the
hub's authorize endpoint; ``callback`` validates the round-tripped ``state``, verifies the token,
resolves a local role, and mints this runner's own session cookie. A hub offering no IdP surface —
probed, never configured — has nothing to bounce to, so the gates go implicit."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import parse_qs, quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from blizzard.auth_core import Role
from blizzard.foundation.clock import IClock
from blizzard.foundation.forwarded import TrustedProxies
from blizzard.foundation.logging import get_logger
from blizzard.runner.auth.jti_cache import IJtiCache
from blizzard.runner.auth.jwks_cache import JwksCache
from blizzard.runner.auth.roles import resolve_local_role
from blizzard.runner.auth.session import (
    SESSION_COOKIE_NAME,
    SESSION_TTL,
    RunnerSession,
    mint_session_cookie,
    verify_session_cookie,
)
from blizzard.runner.auth.validate import FederationTokenError, validate_federation_token
from blizzard.runner.config import RunnerConfig

_log = get_logger("blizzard.runner.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])

_BOUNCE_STATE_COOKIE = "bz_runner_bounce_state"
_BOUNCE_RETURN_COOKIE = "bz_runner_bounce_return"
_BOUNCE_COOKIE_MAX_AGE = 600  # 10 minutes — generous for a slow hub/provider round trip

#: The implicit identity every request resolves to when the hub runs no IdP surface to bounce to.
_IMPLICIT_SESSION = RunnerSession(
    username="operator",
    role=Role.SUPERUSER,
    issued_at=datetime.fromtimestamp(0, tz=UTC),
    expires_at=datetime.fromtimestamp(2**31 - 1, tz=UTC),
)


class NeedsFederationBounce(Exception):
    """Raised by :func:`require_human_session` on a missing/expired session — caught
    by the app-level exception handler and turned into a real ``302`` to
    ``GET /api/auth/login`` (never a bare 401: the human lane is browser-navigated,
    not XHR-driven, so the bounce must be a real redirect a plain page load follows)."""

    def __init__(self, return_to: str) -> None:
        self.return_to = return_to


class HubAuthModeCache:
    """Whether the configured hub runs an IdP surface at all — probed once (a miss costs one
    ``GET /api/auth/jwks.json``) and held for this process's life. A hub whose mode flips after this
    runner started is picked up on the next restart, not live."""

    def __init__(self, http_client: httpx.Client) -> None:
        self._http = http_client
        self._enabled: bool | None = None

    def enabled(self) -> bool:
        if self._enabled is None:
            try:
                resp = self._http.get("/api/auth/jwks.json")
                self._enabled = resp.status_code == httpx.codes.OK
            except httpx.HTTPError as exc:
                _log.warning("hub auth-mode probe failed", detail=str(exc))
                self._enabled = False
        return self._enabled


def _hub_auth_enabled(request: Request) -> bool:
    """Whether the configured hub runs an IdP surface (``auth.mode = "oauth"``) — the one
    switch that decides whether the human lane is gated at all. Probed once and cached
    (:class:`HubAuthModeCache`); ``None`` on the store-free app resolves to *disabled*,
    matching the hermetic default's authless posture."""
    hub_auth_mode: HubAuthModeCache | None = request.app.state.hub_auth_mode
    return hub_auth_mode is not None and hub_auth_mode.enabled()


def _resolve_human_session(request: Request) -> RunnerSession | None:
    """Resolve this request's runner-local session, or ``None`` when the human lane requires one and
    none is validly presented. Two cases grant the implicit identity outright, whatever cookie rode
    along: a **unix-socket peer** (``request.client is None`` — that lane's access control is the
    socket file's filesystem permissions, not an SSO session), and a **``none``-mode hub**, which has
    no IdP surface to bounce to. Otherwise the presented session cookie is verified."""
    if request.client is None:
        return _IMPLICIT_SESSION
    if not _hub_auth_enabled(request):
        return _IMPLICIT_SESSION
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie is None:
        return None
    clock: IClock = request.app.state.clock
    return verify_session_cookie(cookie, secret=request.app.state.session_secret, now=clock.now())


def require_human_session(request: Request) -> RunnerSession:
    """The **served-web-app** gate — the browser-navigated HTML surface mounted at ``/``
    (``runner/app.py``'s ``_gate_web_surface`` middleware). A missing/expired session
    raises :class:`NeedsFederationBounce`, which the app turns into a real ``302`` to
    ``GET /api/auth/login``: a plain page load must *follow* a redirect into the bounce,
    not read a ``401`` body it cannot act on."""
    session = _resolve_human_session(request)
    if session is None:
        raise NeedsFederationBounce(return_to=request.url.path)
    return session


def require_human_api(request: Request) -> RunnerSession:
    """The **human-web-lane API** gate: a missing or expired session is a ``401``, not the served
    surface's ``302``, since a fetch cannot transparently follow a cross-document redirect. Over the
    unix socket and under a ``none``-mode hub this resolves to the implicit identity and never ``401``s
    (:func:`_resolve_human_session`); a **TCP** caller against an oauth-mode hub legitimately gets the
    ``401`` — until CLI session auth lands (issue #96), the socket door is that lane's path."""
    session = _resolve_human_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="runner session required")
    return session


def _safe_return_to(raw: str) -> str:
    """Only a same-origin relative path is honored — mirrors ``hub/api/auth_login.py``'s
    own ``_safe_return_to`` exactly (the same open-redirect concern)."""
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return "/"


def _callback_url(config: RunnerConfig) -> str:
    return f"{config.public_url.rstrip('/')}/api/auth/callback"


@router.get("/login")
def login(request: Request, return_to: str = "/") -> Response:
    config: RunnerConfig = request.app.state.config
    state = secrets.token_urlsafe(24)
    target = (
        f"{config.hub_url.rstrip('/')}/api/auth/authorize"
        f"?client={quote(config.runner_id, safe='')}"
        f"&redirect_uri={quote(_callback_url(config), safe='')}"
        f"&state={quote(state, safe='')}"
        "&response_mode=form_post"
    )
    response = RedirectResponse(target)
    samesite, secure = _bounce_cookie_policy(request)
    response.set_cookie(
        _BOUNCE_STATE_COOKIE,
        state,
        httponly=True,
        samesite=samesite,
        secure=secure,
        max_age=_BOUNCE_COOKIE_MAX_AGE,
    )
    response.set_cookie(
        _BOUNCE_RETURN_COOKIE,
        _safe_return_to(return_to),
        httponly=True,
        samesite=samesite,
        secure=secure,
        max_age=_BOUNCE_COOKIE_MAX_AGE,
    )
    return response


@router.post("/callback")
async def callback(request: Request) -> Response:
    body = (await request.body()).decode()
    parsed = parse_qs(body)
    token = (parsed.get("token") or [None])[0]
    state = (parsed.get("state") or [None])[0]

    expected_state = request.cookies.get(_BOUNCE_STATE_COOKIE)
    if not token or not state or not expected_state or not secrets.compare_digest(expected_state, state):
        return _refused_response("bad or expired state")

    config: RunnerConfig = request.app.state.config
    jwks: JwksCache = request.app.state.jwks_cache
    jti_cache: IJtiCache = request.app.state.jti_cache
    try:
        identity = validate_federation_token(token, runner_id=config.runner_id, jwks=jwks, jti_cache=jti_cache)
    except FederationTokenError as exc:
        _log.warning("federation token refused", detail=str(exc))
        return _refused_response("token refused")

    role = resolve_local_role(config, username=identity.username, hub_role=identity.role)
    clock: IClock = request.app.state.clock
    now = clock.now()
    session = RunnerSession(username=identity.username, role=role, issued_at=now, expires_at=now + SESSION_TTL)
    cookie_value = mint_session_cookie(session, secret=request.app.state.session_secret)

    return_to = _safe_return_to(request.cookies.get(_BOUNCE_RETURN_COOKIE) or "/")
    response = RedirectResponse(return_to, status_code=303)
    response.delete_cookie(_BOUNCE_STATE_COOKIE)
    response.delete_cookie(_BOUNCE_RETURN_COOKIE)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        cookie_value,
        httponly=True,
        samesite="lax",
        secure=_cookie_is_secure(request),
        max_age=int(SESSION_TTL.total_seconds()),
    )
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> Response:
    """Clear the runner's own session cookie (issue #129). Public, like the bounce it complements:
    logging out cannot itself require a live session, and clearing an absent cookie is a harmless no-op.
    The session is a **stateless** signed cookie, so there is nothing server-side to revoke — deleting
    it *is* the logout. If the hub session is still live, the next visit silently re-authenticates
    through the bounce; ending fleet-wide access is hub logout, which stops renewals."""
    response.delete_cookie(SESSION_COOKIE_NAME)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


class RunnerAuthSessionView(BaseModel):
    """An own-identity read (``GET /api/auth/session``, issue #129): whether the human surface is gated
    at all, and if so the signed-in hub username. ``auth_enabled`` false is a ``none``-mode hub, whose
    surface is authless; ``username`` is ``None`` when gated but no valid session is presented."""

    auth_enabled: bool
    username: str | None


@router.get("/session", response_model=RunnerAuthSessionView)
def read_session(request: Request) -> RunnerAuthSessionView:
    """The own-identity read (issue #129). Public and self-resolving: it reports the identity a request
    *would* resolve to rather than gating on one, so it never ``401``s. Under a ``none``-mode hub the
    surface is authless; under oauth it carries the signed-in username, or ``None`` when none rode
    along."""
    if not _hub_auth_enabled(request):
        return RunnerAuthSessionView(auth_enabled=False, username=None)
    session = _resolve_human_session(request)
    return RunnerAuthSessionView(auth_enabled=True, username=session.username if session is not None else None)


#: Origins a browser treats as *potentially trustworthy* regardless of scheme, so a
#: ``Secure`` cookie is both settable and sent back over plain ``http`` there.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _bounce_cookie_policy(request: Request) -> tuple[Literal["lax", "none"], bool]:
    """``SameSite``/``Secure`` for the two short-lived bounce cookies: ``None`` + ``Secure`` wherever a
    browser will accept ``Secure`` (an https or loopback origin), so the cookie survives a cross-site
    ``form_post`` callback; ``Lax`` elsewhere, since a plain-http non-loopback origin cannot hold a
    ``Secure`` cookie at all. The CSRF property is unchanged either way (pinned by
    tests/test_runner_federation.py::test_bounce_cookies_are_samesite_none_secure_on_a_loopback_runner)."""
    if _cookie_is_secure(request) or (request.url.hostname or "").lower() in _LOOPBACK_HOSTS:
        return "none", True
    return "lax", False


def _cookie_is_secure(request: Request) -> bool:
    """Whether the runner's SSO session cookie is minted ``Secure`` — keyed on the
    effective scheme, which honors ``X-Forwarded-Proto`` only when the direct peer is a
    configured trusted proxy (issue #130), so a TLS-terminating reverse proxy in front
    of this runner mints a ``Secure`` cookie while a direct client cannot forge one."""
    trusted: TrustedProxies = request.app.state.trusted_proxies
    scheme = trusted.effective_scheme(
        direct_scheme=request.url.scheme,
        peer=request.client.host if request.client is not None else None,
        forwarded_proto=request.headers.get("x-forwarded-proto"),
    )
    return scheme == "https"


def _refused_response(detail: str) -> Response:
    response = Response(content=detail, status_code=400, media_type="text/plain")
    response.delete_cookie(_BOUNCE_STATE_COOKIE)
    response.delete_cookie(_BOUNCE_RETURN_COOKIE)
    return response


#: Re-exported so ``runner/app.py`` can type-annotate its `Depends` calls without a
#: second import of a name it already has in scope under a different alias.
HumanSession = Annotated[RunnerSession, Depends(require_human_session)]
