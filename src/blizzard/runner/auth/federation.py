"""The runner's SSO federation bounce — ``GET /api/auth/login`` / ``POST /api/auth/callback``,
and the ``require_human_session`` gate every human-web-lane router depends on (issue #95).

**Three-tenant seam.** The runner's API surface is partitioned into three lanes before
any of them is gated (mirroring the hub's own #91 plane table): the worker-hook lane
(asks/facts/heartbeats/leases/escalations/attachments/session-end — called by local
worker processes via ``BLIZZARD_RUNNER_URL``, which cannot SSO-bounce) and the CLI
unix-socket lane (``runner/listeners.py``, filesystem-permission access control) stay
completely outside this module's reach; only the **human web lane** routers declare
``dependencies=[Depends(require_human_session)]`` (``runner/app.py``'s
``include_router`` calls name exactly which).

**The bounce.** A browser hit on a gated route with no valid runner-local session
raises :class:`NeedsFederationBounce`, caught by the app-level exception handler
(``runner/app.py``) and turned into a real ``302`` to ``GET /api/auth/login`` — a
browser therefore completes the whole round trip with no manual navigation (issue #95's
own AC). ``login`` mints a random ``state``, stashes it (and the original
``return_to``) in two short-lived cookies (a double-submit pattern — no server-side
state needed for this leg), and redirects to the hub's own
``GET /api/auth/authorize?client=<runner_id>&redirect_uri=<this runner's own
/api/auth/callback>&state=...&response_mode=form_post``. ``callback`` receives the
hub's auto-submitting form POST, validates the round-tripped ``state`` against the
stashed cookie (login-CSRF), verifies the token
(``runner/auth/validate.py``), resolves a local role (``runner/auth/roles.py``), and
mints the runner's own session cookie (``runner/auth/session.py``) before redirecting
to ``return_to``.

**Authless discovery.** Under a ``none``-mode hub there is no IdP surface to bounce
to at all — the runner discovers this by probing the hub's own JWKS endpoint
(:class:`HubAuthModeCache`) rather than carrying an independent config knob, exactly as
issue #95 specifies, and ``require_human_session`` short-circuits to an implicit
identity with no bounce, mirroring the hub's own ``auth.mode = "none"`` fallback.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import parse_qs, quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

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

#: The implicit identity every request resolves to when the hub itself runs
#: `auth.mode = "none"` (no IdP surface to bounce to) — mirrors the hub's own
#: `IMPLICIT_OPERATOR` (``hub/api/auth_session.py``).
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
    """Whether the configured hub runs an IdP surface at all — probed once (a cache
    miss costs one ``GET /api/auth/jwks.json``) and held for this process's life,
    mirroring how every other startup-resolved fact in this daemon is read once, not
    re-polled per request. A hub flipped from ``oauth`` to ``none`` (or back) after
    this runner started is picked up on the runner's next restart, not live — the
    counterpart of the hub's own key-rotation liveness guarantee is scoped to
    *keys*, not to a mode flip, which issue #95 does not ask this cache to track
    live."""

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


def _resolve_human_session(request: Request) -> RunnerSession | None:
    """Resolve this request's runner-local session, or ``None`` if the human lane
    genuinely requires one and none is validly presented.

    Grants the implicit identity outright in the two cases the human lane is *not*
    gated at all — regardless of what cookie (if any) rode along:

    - **The unix socket.** The runner serves **one** app/route table over **two**
      doors (``runner/listeners.py``: "two doors, not two APIs"); the CLI's local verbs
      (``takeover``, ``requeue``, ``selftest``, ``workspace-prompt``, ``status`` …) dial
      the very same human-lane routes over the socket rather than TCP. That lane's access
      control is the **filesystem permissions on the socket file**, not an SSO session,
      so a socket peer resolves to the implicit identity. ``request.client`` is exactly
      the signal: ``None`` for a unix-domain-socket peer (no ``(host, port)`` to name,
      per uvicorn's own ``get_remote_addr``), a real address for TCP.
    - **A ``none``-mode hub.** With no IdP surface to bounce to, the runner's human
      surface is likewise authless (issue #95), mirroring the hub's own
      ``auth.mode = "none"`` fallback.

    Otherwise the presented session cookie is verified (``None`` on absent/expired/bad)."""
    if request.client is None:
        return _IMPLICIT_SESSION
    hub_auth_mode: HubAuthModeCache | None = request.app.state.hub_auth_mode
    if hub_auth_mode is None or not hub_auth_mode.enabled():
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
    """The **human-web-lane API** gate — the panel's own JSON reads/writes a browser
    reaches over TCP (the runner's status/lease/environment/escalation/fact reads, the
    takeover/requeue/workspace-prompt operator verbs). Declared at ``dependencies=[]`` on
    exactly those routers (``runner/app.py``); the worker-hook lane
    (asks-POST/heartbeat/session-end/attachments/pm-items — workers call these over TCP
    and *cannot* SSO-bounce) and the public routes never carry it.

    A missing/expired session is a ``401``, **not** the web app's ``302``: an XHR/fetch
    from the panel cannot transparently follow a cross-document redirect, so the API
    answers ``401`` and the SPA drives its own navigation to the bounce. Over the unix
    socket and under a ``none``-mode hub this resolves to the implicit identity and never
    ``401``s (see :func:`_resolve_human_session`); a CLI dialling one of these routes over
    ``--runner-url`` **TCP** against an oauth-mode hub legitimately gets the ``401`` —
    CLI session auth is issue #96, and until then the socket door is that lane's path."""
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
    response.set_cookie(_BOUNCE_STATE_COOKIE, state, httponly=True, samesite="lax", max_age=_BOUNCE_COOKIE_MAX_AGE)
    response.set_cookie(
        _BOUNCE_RETURN_COOKIE,
        _safe_return_to(return_to),
        httponly=True,
        samesite="lax",
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
