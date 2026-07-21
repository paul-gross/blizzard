"""The provider-login surface — ``GET /api/auth/providers``, ``GET /api/auth/{name}/
authorize``, ``GET /api/auth/{name}/callback``, ``POST /api/auth/logout`` (issue #92).

Public plane throughout — no ``require(<permission>)``: an unauthenticated visitor must
reach these to log in at all. Under ``auth.mode = "none"`` every route here is inert
(``providers`` empty, ``authorize``/``callback`` 404) — there is no login mechanism to
run, mirroring #95's own "no IdP surface under none".

The route stays a deterministic shell over the provider seam (``bzh:deterministic-shell``):
all provider wire-shape/JWT/httpx knowledge lives in ``hub/auth/oauth/internal/``; this
module only orchestrates ``state`` issuance, the provider's ``authorize_url``/``exchange``,
the domain's ``link_or_mint``/``mint_session``, and the cookie.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from blizzard.hub.api.auth_session import _SESSION_COOKIE_NAME, _presented_session_id
from blizzard.hub.api.deps import get_services
from blizzard.hub.auth.facts import AuthFactsService
from blizzard.hub.auth.hashing import hash_session_id
from blizzard.hub.auth.oauth.provider import OAuthExchangeError
from blizzard.hub.auth.service import ABSOLUTE_MAX_AGE, PROVIDER_LOGIN_STATE_KIND
from blizzard.hub.config import AUTH_MODE_NONE

router = APIRouter(prefix="/api/auth", tags=["auth"])

_THROTTLE_DETAIL = "too many login attempts — try again shortly"


class ProviderSummary(BaseModel):
    """One configured provider, as ``GET /api/auth/providers`` lists it."""

    name: str
    display_name: str
    type: str


def _client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _safe_return_to(raw: str | None) -> str:
    """Only a same-origin relative path is honored — anything else (an absolute URL,
    a protocol-relative ``//host`` — an open-redirect vector) falls back to ``/``."""
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return "/"


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
    services = get_services(request)
    if not services.auth_throttle.allow(_client_ip(request)):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=_THROTTLE_DETAIL)
    provider = services.oauth_providers.get(name)
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown provider {name!r}")
    state = services.auth.start_state(
        kind=PROVIDER_LOGIN_STATE_KIND, provider_name=name, return_to=_safe_return_to(return_to)
    )
    redirect_uri = _callback_url(request, name)
    return RedirectResponse(provider.authorize_url(state=state, redirect_uri=redirect_uri))


@router.get("/{name}/callback")
def callback(name: str, request: Request, code: str | None = None, state: str | None = None) -> Response:
    if request.app.state.config.auth.mode == AUTH_MODE_NONE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="login is not enabled")
    services = get_services(request)
    if not services.auth_throttle.allow(_client_ip(request)):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=_THROTTLE_DETAIL)

    entry = services.auth.consume_state(state) if state else None
    if entry is None or entry.kind != PROVIDER_LOGIN_STATE_KIND:
        return _login_failed(
            services.auth_facts, actor=_client_ip(request), subject=name, detail="bad or expired state"
        )
    if entry.provider_name != name:
        # A state minted for one provider presented to another's callback — a
        # cross-provider replay/tamper attempt, refused outright rather than treated as
        # a plain expired/missing state.
        services.auth_facts.sso_refused(
            actor=_client_ip(request),
            subject=name,
            detail=f"state minted for provider {entry.provider_name!r}",
        )
        return _error_response(status.HTTP_400_BAD_REQUEST, "sso_refused")

    provider = services.oauth_providers.get(name)
    if provider is None or code is None:
        return _login_failed(services.auth_facts, actor=_client_ip(request), subject=name, detail="missing code")

    try:
        identity = provider.exchange(code=code, redirect_uri=_callback_url(request, name))
    except OAuthExchangeError as exc:
        return _login_failed(services.auth_facts, actor=_client_ip(request), subject=name, detail=str(exc))

    user = services.auth.link_or_mint(identity, provider_name=name)
    plaintext, _session = services.auth.mint_session(user)

    response = RedirectResponse(entry.return_to, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        _SESSION_COOKIE_NAME,
        plaintext,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=int(ABSOLUTE_MAX_AGE.total_seconds()),
    )
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response) -> Response:
    mode = request.app.state.config.auth.mode
    if mode != AUTH_MODE_NONE:
        services = get_services(request)
        session_id = _presented_session_id(request)
        if session_id is not None:
            session = services.sessions.get_by_hash(hash_session_id(session_id))
            if session is not None:
                services.auth.revoke(session)
    response.delete_cookie(_SESSION_COOKIE_NAME)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


def _callback_url(request: Request, name: str) -> str:
    return f"{str(request.base_url).rstrip('/')}/api/auth/{name}/callback"


def _login_failed(facts: AuthFactsService, *, actor: str, subject: str, detail: str) -> Response:
    facts.login_failed(actor=actor, subject=subject, detail=detail)
    return _error_response(status.HTTP_400_BAD_REQUEST, "login_failed")


def _error_response(status_code: int, error: str) -> Response:
    return JSONResponse(status_code=status_code, content={"error": error})
