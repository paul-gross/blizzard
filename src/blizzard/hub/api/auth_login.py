"""The provider-login surface — providers, authorize, callback, logout.

A sign-in started from an invitation link carries the link's token as ``invite``; ``state`` holds only its hash
through the provider round trip. On the callback the token resolves through the hub-scoped invitation reads, and a
sign-in that accepts it lands on the board's one-shot :data:`INVITATION_LANDING` naming the invited tenant.

The hub is invite-only: a sign-in that admits nobody — a person the hub does not know with no invitation, or an
invitation that is unusable or names a different email — writes nothing, sets no cookie, and lands on the board's
login page as ``/login?refusal=<code>`` (:data:`SIGN_IN_REFUSED`), the code a :class:`SignInRefusal`. A person
already signed in keeps the session they hold.

Public plane throughout — no ``require(<permission>)``: an unauthenticated visitor must
reach these to log in at all. Under ``auth.mode = "none"`` every route here is inert
(``providers`` empty, ``authorize``/``callback`` 404).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from urllib.parse import urlencode

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import APIRouter, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from blizzard.foundation.forwarded import TrustedProxies
from blizzard.foundation.origin import Origin
from blizzard.foundation.registry.interface import ServiceAbsent
from blizzard.foundation.return_to import ReturnTo
from blizzard.foundation.sign_in_refusal import SignInRefusal
from blizzard.hub.api import problems
from blizzard.hub.api.auth_session import SESSION_COOKIE_NAME, PresentedSession
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.api.problem_rendering import problem_response
from blizzard.hub.api.request_config import config_for_request
from blizzard.hub.auth.facts import AuthFactsService
from blizzard.hub.auth.invitations import InvitationToken
from blizzard.hub.auth.oauth.provider import IOAuthProvider, OAuthExchangeError
from blizzard.hub.auth.oauth.registry import IOAuthProviderRegistry
from blizzard.hub.auth.service import ABSOLUTE_MAX_AGE, PROVIDER_LOGIN_STATE_KIND, AuthService, InvitationAccepted
from blizzard.hub.auth.sessions import IReadSessionRepository
from blizzard.hub.auth.sign_in import SignIn
from blizzard.hub.auth.throttle import LiveIpThrottle
from blizzard.hub.config import AUTH_MODE_NONE, HubConfig
from blizzard.hub.events.brokers import TenantBrokers
from blizzard.hub.settings.source import HubConfigSource

router = APIRouter(prefix="/api/auth", tags=["auth"])


#: The board route a sign-in that accepted an invitation lands on, its ``tenant`` naming the invited tenant.
INVITATION_LANDING = "/invite/accepted"

#: The board route a sign-in that admitted nobody lands on, its ``refusal`` naming why.
SIGN_IN_REFUSED = "/login"


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
    instance: str

    def login_failed(self, detail: str) -> Response:
        self.facts.login_failed(actor=self.actor, subject=self.subject, detail=detail)
        return problem_response(problems.login_failed_problem(instance=self.instance))

    def sso_refused(self, detail: str) -> Response:
        self.facts.sso_refused(actor=self.actor, subject=self.subject, detail=detail)
        return problem_response(problems.sso_refused_problem(instance=self.instance))


@dataclass(frozen=True)
class ProviderLogin:
    """One call at a named provider's login routes, and the hub state it is judged against."""

    request: Request
    name: str
    config: HubConfig
    trusted_proxies: TrustedProxies
    providers: IOAuthProviderRegistry
    facts: AuthFactsService
    throttle: LiveIpThrottle

    @classmethod
    def of(
        cls,
        request: Request,
        name: str,
        *,
        config_source: HubConfigSource,
        trusted_proxies: TrustedProxies,
        providers: IOAuthProviderRegistry,
        facts: AuthFactsService,
        throttle: LiveIpThrottle,
    ) -> ProviderLogin:
        return cls(
            request, name, config_for_request(request, config_source), trusted_proxies, providers, facts, throttle
        )

    @property
    def origin(self) -> Origin:
        return Origin(self.request, self.trusted_proxies)

    @property
    def provider(self) -> IOAuthProvider | None:
        return self.providers.get(self.name)

    @property
    def callback_url(self) -> str:
        return f"{str(self.request.base_url).rstrip('/')}/api/auth/{self.name}/callback"

    @property
    def refusal(self) -> LoginRefusal:
        return LoginRefusal(self.facts, actor=self.origin.ip, subject=self.name, instance=self.request.url.path)

    def assert_not_throttled(self) -> None:
        if not self.throttle.allow(self.origin.ip, self.request.state.auth_throttle_generation):
            raise ProblemHTTPException(problems.login_throttled_problem)


@router.get("/providers", response_model=list[ProviderSummary])
@inject_sync
def list_providers(request: Request, providers: FromDishka[IOAuthProviderRegistry | None]) -> list[ProviderSummary]:
    if request.app.state.config.auth.mode == AUTH_MODE_NONE:
        return []
    if providers is None:
        raise ServiceAbsent("OAuth provider registry")
    return [ProviderSummary(name=p.name, display_name=p.display_name, type=p.type) for p in providers.list()]


@router.get("/{name}/authorize", response_model=None)
@inject_sync
def authorize(
    name: str,
    request: Request,
    auth: FromDishka[AuthService],
    trusted_proxies: FromDishka[TrustedProxies],
    providers: FromDishka[IOAuthProviderRegistry],
    facts: FromDishka[AuthFactsService],
    throttle: FromDishka[LiveIpThrottle],
    config_source: FromDishka[HubConfigSource],
    return_to: str | None = None,
    invite: str | None = None,
) -> RedirectResponse:
    login = ProviderLogin.of(
        request,
        name,
        config_source=config_source,
        trusted_proxies=trusted_proxies,
        providers=providers,
        facts=facts,
        throttle=throttle,
    )
    if login.config.auth.mode == AUTH_MODE_NONE:
        raise ProblemHTTPException(problems.login_disabled_problem)
    login.assert_not_throttled()
    provider = login.provider
    if provider is None:
        raise ProblemHTTPException(partial(problems.login_provider_unknown_problem, name))
    state = auth.start_state(
        kind=PROVIDER_LOGIN_STATE_KIND,
        provider_name=name,
        return_to=ReturnTo(return_to).safe,
        invitation_token_hash=InvitationToken(invite).hash if invite else None,
    )
    return RedirectResponse(provider.authorize_url(state=state, redirect_uri=login.callback_url))


@router.get("/{name}/callback", response_model=None)
@inject_sync
def callback(
    name: str,
    request: Request,
    auth: FromDishka[AuthService],
    sign_in: FromDishka[SignIn],
    trusted_proxies: FromDishka[TrustedProxies],
    providers: FromDishka[IOAuthProviderRegistry],
    facts: FromDishka[AuthFactsService],
    throttle: FromDishka[LiveIpThrottle],
    config_source: FromDishka[HubConfigSource],
    code: str | None = None,
    state: str | None = None,
) -> Response:
    login = ProviderLogin.of(
        request,
        name,
        config_source=config_source,
        trusted_proxies=trusted_proxies,
        providers=providers,
        facts=facts,
        throttle=throttle,
    )
    if login.config.auth.mode == AUTH_MODE_NONE:
        raise ProblemHTTPException(problems.login_disabled_problem)
    login.assert_not_throttled()
    refusal = login.refusal

    entry = auth.consume_state(state) if state else None
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

    admitted = sign_in.admit(identity, provider_name=name, invitation_token_hash=entry.invitation_token_hash)
    if isinstance(admitted, SignInRefusal):
        refused = f"{SIGN_IN_REFUSED}?{urlencode({'refusal': admitted.value})}"
        return RedirectResponse(refused, status_code=status.HTTP_302_FOUND)
    if isinstance(admitted, InvitationAccepted):
        plaintext = admitted.session_id
        landing = f"{INVITATION_LANDING}?{urlencode({'tenant': admitted.tenant})}"
    else:
        plaintext, landing = admitted.session_id, entry.return_to

    response = RedirectResponse(landing, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        plaintext,
        httponly=True,
        samesite="lax",
        secure=login.origin.secure,
        max_age=int(ABSOLUTE_MAX_AGE.total_seconds()),
    )
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
@inject_sync
def logout(
    request: Request,
    response: Response,
    sessions: FromDishka[IReadSessionRepository | None],
    auth: FromDishka[AuthService | None],
    brokers: FromDishka[TenantBrokers],
) -> Response:
    mode = request.app.state.config.auth.mode
    if mode != AUTH_MODE_NONE:
        id_hash = PresentedSession(request).id_hash
        if id_hash is not None:
            if sessions is None or auth is None:
                raise ServiceAbsent("session services")
            session = sessions.get_by_hash(id_hash)
            if session is not None:
                auth.revoke(session)
                # The delete has committed: the session's open event streams end in every tenant, and every
                # other session of its user keeps streaming.
                brokers.end_session(id_hash)
    response.delete_cookie(SESSION_COOKIE_NAME)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
