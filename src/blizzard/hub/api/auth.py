"""Runner-bearer-token authentication at the hub's edge.

A presented token resolves by sha256-hex-digest lookup against the stored hash column;
that selection **is** the match, so no separate ``hmac.compare_digest`` is load-bearing.
The lookup spans tenants — no tenant is known until it answers — so it runs through the
hub-scoped credential reads and names the runner's tenant; the registration itself is then read
through that tenant's own registry.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.attributes import CALLER, annotate
from blizzard.foundation.roles import dto
from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.foundation.tokens import TokenHash
from blizzard.hub.api.bearer import presented_bearer
from blizzard.hub.api.hub_level import Hub
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.observability.tracing.attributes import RUNNER_ID, RUNNER_NAME
from blizzard.hub.domain.runners.registration import RunnerRegistration, RunnerTokenRefused, refuse_runner_token
from blizzard.hub.tenancy import TenantClosed, TenantId, UnknownTenant

_log = get_logger("blizzard.hub.auth")

#: ASGI-scope key holding the principal the trace gate already resolved, so the route's own auth
#: dependency reuses it rather than resolving the same token a second time.
_PRINCIPAL_SCOPE_KEY = "blizzard.runner_principal"

#: ASGI-scope key holding the registration that principal was resolved from, so the fleet gate asks the
#: loaded model whether it is retired rather than reading the registry a second time.
_REGISTRATION_SCOPE_KEY = "blizzard.runner_registration"


@dto
@dataclass(frozen=True)
class RunnerPrincipal:
    """A bearer token resolved to the runner it belongs to: its id, the name it holds, its
    workspace — ``None`` until the runner first registers — and the tenant it was added in, which is
    the tenant of every call it makes."""

    runner_id: str
    runner_name: str
    workspace_id: str | None
    tenant_id: TenantId


@dataclass(frozen=True)
class RunnerAuth:
    """One request's runner-bearer decision — resolution stays separate from what each
    router does with it: the fleet router demands a principal, the operator routers refuse one,
    and the identity route answers the verdict itself."""

    request: Request
    services: HubServices

    @classmethod
    def of(cls, request: Request, services: HubServices) -> RunnerAuth:
        return cls(request, services)

    @property
    def principal(self) -> RunnerPrincipal | None:  # ast-grep-ignore: bzh:property-delegates
        """The presented token resolved to its runner, or ``None`` when the header is
        missing/malformed or the token does not resolve — no rejection."""
        scope = self.request.scope
        if _PRINCIPAL_SCOPE_KEY not in scope:
            scope[_PRINCIPAL_SCOPE_KEY] = self._resolve()
        return scope[_PRINCIPAL_SCOPE_KEY]

    def _resolved_registration(self) -> RunnerRegistration | None:
        return self.request.scope.get(_REGISTRATION_SCOPE_KEY)

    def _resolve(self) -> RunnerPrincipal | None:
        token = presented_bearer(self.request)
        if token is None:
            return None
        resolved = self.services.hub_scoped_reads.credentials.runner_for_token_hash(TokenHash(token).hex)
        if resolved is None:
            return None
        registration = _registration_in_tenant(self.services, resolved.tenant, resolved.runner_id)
        if registration is None:
            return None
        self.request.scope[_REGISTRATION_SCOPE_KEY] = registration
        return RunnerPrincipal(
            runner_id=registration.runner_id,
            runner_name=registration.name,
            workspace_id=registration.workspace_id,
            tenant_id=resolved.tenant,
        )

    def demand(self, *, allow_retired: bool = False) -> RunnerPrincipal:
        """The resolved principal; a missing/malformed header, a revoked token, or one that
        resolves to no runner raises 401 — there is no tokenless fleet call. A principal whose
        registration is retired raises :class:`RunnerRetired` (403) unless ``allow_retired``: a token
        that outlived its retirement (an enroll racing the retire) is refused as contact."""
        principal = self.principal
        if principal is not None:
            annotate({CALLER: "runner", RUNNER_ID: principal.runner_id, RUNNER_NAME: principal.runner_name})
            registration = self._resolved_registration()
            if registration is not None and not allow_retired:
                registration.refuse_if_retired(action="fleet call")
            return principal
        token = presented_bearer(self.request)
        credentials = self.services.hub_scoped_reads.credentials
        if token is not None and credentials.revoked_token_runner(TokenHash(token).hex) is not None:
            _log.warning("revoked runner token presented", path=self.request.url.path)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bearer token has been revoked")
        reason = (
            "missing or malformed Authorization header"
            if token is None
            else "bearer token does not resolve to a known runner"
        )
        _log.warning("runner auth failed", reason=reason, path=self.request.url.path)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=reason)

    def identify(self) -> RunnerRegistration:
        """The runner the presented token names, or :class:`RunnerTokenRefused` saying why the hub
        refuses it — ``missing`` with no token, else the verdict of :func:`refuse_runner_token` over
        the registration holding the token's hash and, when none does, the runner a revoked hash was
        issued to. Reads only."""
        token = presented_bearer(self.request)
        if token is None:
            raise RunnerTokenRefused(RunnerTokenRefusalReason.MISSING)
        token_hash = TokenHash(token).hex
        credentials = self.services.hub_scoped_reads.credentials
        resolved = credentials.runner_for_token_hash(token_hash)
        current = None
        if resolved is not None:
            current = _registration_in_tenant(self.services, resolved.tenant, resolved.runner_id)
        revoked_for = None
        if current is None and (revoked := credentials.revoked_token_runner(token_hash)) is not None:
            revoked_for = _registration_in_tenant(self.services, revoked.tenant, revoked.runner_id)
        return refuse_runner_token(current, revoked_for=revoked_for)

    def refuse_runner(self) -> None:
        """Refuse a runner's token on an operator router with 403 — valid only on the fleet
        router. An unresolvable token is not flagged: that is what an anonymous operator call
        looks like."""
        principal = self.principal
        if principal is None:
            return
        _log.warning(
            "runner token presented on operator verb", runner_id=principal.runner_id, path=self.request.url.path
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"runner token for {principal.runner_id!r} is not valid on an operator verb",
        )


def _registration_in_tenant(services: HubServices, tenant: TenantId, runner_id: str) -> RunnerRegistration | None:
    """``runner_id``'s registration, read through the registry of ``tenant`` — the tenant a credential
    resolved it to — or ``None`` when that tenant has no registration of it or no stores to open."""
    try:
        registry = services.tenant_registry(tenant)
    except (UnknownTenant, TenantClosed):
        return None
    return registry.get_runner(runner_id)


@dto
@dataclass(frozen=True)
class RunnerSigningIn:
    """The runner a sign-in names, read in the tenant it was added in."""

    tenant: TenantId
    registration: RunnerRegistration


def runner_signing_in(services: HubServices, client: str) -> RunnerSigningIn | None:
    """The runner a sign-in names as its ``client``, read in the runner's own tenant, or ``None``
    when no open tenant holds a runner of that id. A runner id is hub-minted, so the client alone
    settles the tenant."""
    resolved = services.hub_scoped_reads.credentials.runner_by_id(client)
    if resolved is None:
        return None
    registration = _registration_in_tenant(services, resolved.tenant, resolved.runner_id)
    return None if registration is None else RunnerSigningIn(resolved.tenant, registration)


def require_runner_principal(request: Request, services: Hub) -> RunnerPrincipal:
    return RunnerAuth.of(request, services).demand()


def require_runner_principal_even_if_retired(request: Request, services: Hub) -> RunnerPrincipal:
    """The principal without the retirement refusal — the claim route's, whose retired refusal is the
    paused-denial body a runner parses (``ClaimService`` raises it in-domain)."""
    return RunnerAuth.of(request, services).demand(allow_retired=True)


def reject_runner_principal(request: Request, services: Hub) -> None:
    RunnerAuth.of(request, services).refuse_runner()
