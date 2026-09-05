"""The runner's SSO federation bounce, and the gates the human web lane depends on (issue #95).

``login`` stashes a random ``state`` and ``return_to`` in two short-lived cookies and redirects to the
hub's authorize endpoint; ``callback`` validates the round-tripped ``state``, verifies the token,
resolves a local role, and mints this runner's own session cookie."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import parse_qs, quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from blizzard.auth_core import Role
from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.origin import Origin
from blizzard.foundation.return_to import ReturnTo
from blizzard.runner.auth.jti_cache import IJtiCache
from blizzard.runner.auth.jwks_cache import JwksCache
from blizzard.runner.auth.roles import LocalRole
from blizzard.runner.auth.session import SESSION_COOKIE_NAME, SESSION_TTL, RunnerSession, SessionCookie
from blizzard.runner.auth.validate import FederationToken, FederationTokenError
from blizzard.runner.config import CALLBACK_PATH, RunnerConfig

_log = get_logger("blizzard.runner.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])

_BOUNCE_STATE_COOKIE = "bz_runner_bounce_state"
_BOUNCE_RETURN_COOKIE = "bz_runner_bounce_return"
_BOUNCE_COOKIE_MAX_AGE = 600  # 10 minutes — generous for a slow hub/provider round trip

#: Origins a browser treats as potentially trustworthy whatever the scheme, so ``Secure`` holds over plain http.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

#: The implicit identity every request resolves to when the hub runs no IdP surface to bounce to.
_IMPLICIT_SESSION = RunnerSession(
    username="operator",
    role=Role.SUPERUSER,
    issued_at=datetime.fromtimestamp(0, tz=UTC),
    expires_at=datetime.fromtimestamp(2**31 - 1, tz=UTC),
)


class NeedsFederationBounce(Exception):
    """A missing or expired session on the browser-navigated surface: a plain page load must be
    redirected into ``GET /api/auth/login``, never handed a ``401`` body it cannot act on."""

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


@dataclass(frozen=True)
class HumanLane:
    """One request's runner-local identity — resolution stays separate from what each surface
    demands of it: the served web app bounces, the human-lane API ``401``s."""

    request: Request

    @property
    def gated(self) -> bool:
        """Whether the hub offers an IdP surface to bounce to (:class:`HubAuthModeCache`) — ``None``
        on the store-free app resolves to *ungated*, matching the hermetic default's authless posture."""
        cache: HubAuthModeCache | None = self.request.app.state.hub_auth_mode
        return cache is not None and cache.enabled()

    @property
    def session(self) -> RunnerSession | None:
        """The presented session, or ``None`` when this lane is gated and none validly rode along.
        Two cases grant the implicit identity outright, whatever cookie came with them: a
        **unix-socket peer** (``request.client is None``, whose access control is the socket file's
        permissions) and an **ungated hub**."""
        if self.request.client is None:
            return _IMPLICIT_SESSION
        if not self.gated:
            return _IMPLICIT_SESSION
        cookie = self.request.cookies.get(SESSION_COOKIE_NAME)
        if cookie is None:
            return None
        clock: IClock = self.request.app.state.clock
        return SessionCookie(self.request.app.state.session_secret).read(cookie, now=clock.now())

    def demand_web(self) -> RunnerSession:
        """The served-web-app gate — the browser-navigated HTML surface mounted at ``/``."""
        session = self.session
        if session is None:
            raise NeedsFederationBounce(return_to=self.request.url.path)
        return session

    def demand_api(self) -> RunnerSession:
        """The human-web-lane API gate: a ``401``, not the served surface's ``302``, since a fetch
        cannot transparently follow a cross-document redirect. A **TCP** caller against a gated hub
        legitimately gets it — until CLI session auth lands (issue #96), the socket door is that lane's path."""
        session = self.session
        if session is None:
            raise HTTPException(status_code=401, detail="runner session required")
        return session


@dataclass(frozen=True)
class Bounce:
    """The two short-lived cookies a federation round trip rides on: the ``state`` the callback
    validates against, and where to land once it succeeds."""

    request: Request

    @property
    def origin(self) -> Origin:
        return Origin(self.request, self.request.app.state.trusted_proxies)

    @property
    def state(self) -> str | None:
        return self.request.cookies.get(_BOUNCE_STATE_COOKIE)

    @property
    def return_to(self) -> str:
        return ReturnTo(self.request.cookies.get(_BOUNCE_RETURN_COOKIE)).safe

    @property
    def policy(self) -> tuple[Literal["lax", "none"], bool]:
        """``SameSite``/``Secure``: ``None`` + ``Secure`` wherever a browser will accept ``Secure`` (an
        https or loopback origin), so the cookie survives the cross-site ``form_post`` callback; ``Lax``
        elsewhere, where a ``Secure`` cookie cannot be held at all (pinned by
        tests/test_runner_federation.py::test_bounce_cookies_are_samesite_none_secure_on_a_loopback_runner)."""
        if self.origin.secure or (self.request.url.hostname or "").lower() in _LOOPBACK_HOSTS:
            return "none", True
        return "lax", False

    def issue(self, response: Response, *, state: str, return_to: str) -> None:
        samesite, secure = self.policy
        for name, value in ((_BOUNCE_STATE_COOKIE, state), (_BOUNCE_RETURN_COOKIE, ReturnTo(return_to).safe)):
            response.set_cookie(
                name, value, httponly=True, samesite=samesite, secure=secure, max_age=_BOUNCE_COOKIE_MAX_AGE
            )

    def clear(self, response: Response) -> None:
        response.delete_cookie(_BOUNCE_STATE_COOKIE)
        response.delete_cookie(_BOUNCE_RETURN_COOKIE)

    def matches(self, presented: str | None) -> bool:
        expected = self.state
        if not presented or not expected:
            return False
        return secrets.compare_digest(expected, presented)

    def refuse(self, detail: str) -> Response:
        response = Response(content=detail, status_code=400, media_type="text/plain")
        self.clear(response)
        return response


def require_human_session(request: Request) -> RunnerSession:
    return HumanLane(request).demand_web()


def require_human_api(request: Request) -> RunnerSession:
    return HumanLane(request).demand_api()


def _callback_url(request: Request, config: RunnerConfig) -> str:
    """The callback this bounce presents: the declared origin the browser actually reached, so the hub's
    cross-site ``form_post`` lands where the bounce cookies live. Selection is membership in the declared
    set, never construction from the request; `docs/deployment/human-auth.md` §Runner-side federation owns why."""
    origins = config.public_origins
    arrived = request.headers.get("host")
    chosen = origins.select(arrived)
    if chosen is None:
        # The bounce still completes against the canonical origin, which is registered — but the cookies
        # were set on this request's origin, so a remote browser dead-ends on `bad or expired state`.
        _log.warning(
            "no declared origin matches the arriving Host — falling back to the canonical origin",
            arrived_host=arrived,
            declared=list(origins.urls),
            falling_back_to=origins.canonical,
        )
    return f"{chosen or origins.canonical or ''}{CALLBACK_PATH}"


@router.get("/login")
def login(request: Request, return_to: str = "/") -> Response:
    config: RunnerConfig = request.app.state.config
    state = secrets.token_urlsafe(24)
    callback_url = _callback_url(request, config)
    target = (
        f"{config.hub_url.rstrip('/')}/api/auth/authorize"
        f"?client={quote(config.runner_id, safe='')}"
        f"&redirect_uri={quote(callback_url, safe='')}"
        f"&state={quote(state, safe='')}"
        "&response_mode=form_post"
    )
    response = RedirectResponse(target)
    Bounce(request).issue(response, state=state, return_to=return_to)
    return response


@router.post("/callback")
async def callback(request: Request) -> Response:
    body = (await request.body()).decode()
    parsed = parse_qs(body)
    token = (parsed.get("token") or [None])[0]
    state = (parsed.get("state") or [None])[0]

    bounce = Bounce(request)
    if not token or not bounce.matches(state):
        return bounce.refuse("bad or expired state")

    config: RunnerConfig = request.app.state.config
    jwks: JwksCache = request.app.state.jwks_cache
    jti_cache: IJtiCache = request.app.state.jti_cache
    clock: IClock = request.app.state.clock
    try:
        identity = FederationToken(
            token, runner_id=config.runner_id, jwks=jwks, jti_cache=jti_cache, clock=clock
        ).identity()
    except FederationTokenError as exc:
        _log.warning("federation token refused", detail=str(exc))
        return bounce.refuse("token refused")

    role = LocalRole(config, username=identity.username, hub_role=identity.role).role
    now = clock.now()
    session = RunnerSession(username=identity.username, role=role, issued_at=now, expires_at=now + SESSION_TTL)
    cookie_value = SessionCookie(request.app.state.session_secret).mint(session)

    response = RedirectResponse(bounce.return_to, status_code=303)
    bounce.clear(response)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        cookie_value,
        httponly=True,
        samesite="lax",
        secure=bounce.origin.secure,
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
    lane = HumanLane(request)
    if not lane.gated:
        return RunnerAuthSessionView(auth_enabled=False, username=None)
    session = lane.session
    return RunnerAuthSessionView(auth_enabled=True, username=session.username if session is not None else None)


HumanSession = Annotated[RunnerSession, Depends(require_human_session)]
