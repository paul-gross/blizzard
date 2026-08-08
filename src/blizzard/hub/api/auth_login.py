"""The provider-login surface (issue #92) — providers, authorize, callback, logout.

Public plane throughout — no ``require(<permission>)``: an unauthenticated visitor must
reach these to log in at all. Under ``auth.mode = "none"`` every route here is inert
(``providers`` empty, ``authorize``/``callback`` 404).
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from blizzard.foundation.origin import Origin
from blizzard.foundation.return_to import ReturnTo
from blizzard.hub.api.auth_session import _SESSION_COOKIE_NAME, PresentedSession
from blizzard.hub.api.deps import get_services
from blizzard.hub.auth.facts import AuthFactsService
from blizzard.hub.auth.oauth.provider import IOAuthProvider, OAuthExchangeError
from blizzard.hub.auth.service import ABSOLUTE_MAX_AGE, PROVIDER_LOGIN_STATE_KIND
from blizzard.hub.composition import HubServices
from blizzard.hub.config import AUTH_MODE_NONE

router = APIRouter(prefix="/api/auth", tags=["auth"])

_THROTTLE_DETAIL = "too many login attempts — try again shortly"


class ProviderSummary(BaseModel):
    """One configured provider, as ``GET /api/auth/providers`` lists it."""

    name: str
    display_name: str
    type: str


@dataclass(frozen=True)
class LoginRefusal:
    """A refused login attempt against one provider: the fact it records, and the opaque
    ``400`` the browser is handed — never which of the checks it failed."""

    facts: AuthFactsService
    actor: str
    subject: str

    def login_failed(self, detail: str) -> Response:
        self.facts.login_failed(actor=self.actor, subject=self.subject, detail=detail)
        return self._response("login_failed")

    def sso_refused(self, detail: str) -> Response:
        self.facts.sso_refused(actor=self.actor, subject=self.subject, detail=detail)
        return self._response("sso_refused")

    def _response(self, error: str) -> Response:
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"error": error})


@dataclass(frozen=True)
class ProviderLogin:
    """One call at a named provider's login routes, and the hub state it is judged against."""

    request: Request
    services: HubServices
    name: str

    @classmethod
    def of(cls, request: Request, name: str) -> ProviderLogin:
        return cls(request, get_services(request), name)

    @property
    def origin(self) -> Origin:
        return Origin(self.request, self.services.trusted_proxies)

    @property
    def provider(self) -> IOAuthProvider | None:
        return self.services.oauth_providers.get(self.name)

    @property
    def callback_url(self) -> str:
        return f"{str(self.request.base_url).rstrip('/')}/api/auth/{self.name}/callback"

    @property
    def refusal(self) -> LoginRefusal:
        return LoginRefusal(self.services.auth_facts, actor=self.origin.ip, subject=self.name)

    def assert_not_throttled(self) -> None:
        if not self.services.auth_throttle.allow(self.origin.ip):
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=_THROTTLE_DETAIL)


@router.get("/providers", response_model=list[ProviderSummary])
def list_providers(request: Request) -> list[ProviderSummary]:
    if request.app.state.config.auth.mode == AUTH_MODE_NONE:
        return []
    services = get_services(request)
    return [
        ProviderSummary(name=p.name, display_name=p.display_name, type=p.type) for p in services.oauth_providers.list()
    ]


@router.get("/{name}/authorize")
def authorize(name: str, request: Request, return_to: str | None = None) -> RedirectResponse:
    if request.app.state.config.auth.mode == AUTH_MODE_NONE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="login is not enabled")
    login = ProviderLogin.of(request, name)
    login.assert_not_throttled()
    provider = login.provider
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown provider {name!r}")
    state = login.services.auth.start_state(
        kind=PROVIDER_LOGIN_STATE_KIND, provider_name=name, return_to=ReturnTo(return_to).safe
    )
    return RedirectResponse(provider.authorize_url(state=state, redirect_uri=login.callback_url))


@router.get("/{name}/callback")
def callback(name: str, request: Request, code: str | None = None, state: str | None = None) -> Response:
    if request.app.state.config.auth.mode == AUTH_MODE_NONE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="login is not enabled")
    login = ProviderLogin.of(request, name)
    login.assert_not_throttled()
    refusal = login.refusal

    entry = login.services.auth.consume_state(state) if state else None
    if entry is None or entry.kind != PROVIDER_LOGIN_STATE_KIND:
        return refusal.login_failed("bad or expired state")
    if entry.provider_name != name:
        # A state minted for one provider presented to another's callback — a replay/tamper attempt.
        return refusal.sso_refused(f"state minted for provider {entry.provider_name!r}")

    provider = login.provider
    if provider is None or code is None:
        return refusal.login_failed("missing code")

    try:
        identity = provider.exchange(code=code, redirect_uri=login.callback_url)
    except OAuthExchangeError as exc:
        return refusal.login_failed(str(exc))

    user = login.services.auth.link_or_mint(identity, provider_name=name)
    plaintext, _session = login.services.auth.mint_session(user)

    response = RedirectResponse(entry.return_to, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        _SESSION_COOKIE_NAME,
        plaintext,
        httponly=True,
        samesite="lax",
        secure=login.origin.secure,
        max_age=int(ABSOLUTE_MAX_AGE.total_seconds()),
    )
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response) -> Response:
    mode = request.app.state.config.auth.mode
    if mode != AUTH_MODE_NONE:
        services = get_services(request)
        id_hash = PresentedSession(request).id_hash
        if id_hash is not None:
            session = services.sessions.get_by_hash(id_hash)
            if session is not None:
                services.auth.revoke(session)
    response.delete_cookie(_SESSION_COOKIE_NAME)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
