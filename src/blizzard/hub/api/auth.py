"""Runner-bearer-token authentication at the hub's edge.

A presented token resolves by sha256-hex-digest lookup against the stored hash column;
that selection **is** the match, so no separate ``hmac.compare_digest`` is load-bearing.
The lookup spans tenants — no tenant is known until it answers — so it runs through the
hub-scoped credential reads and names the runner's tenant; the registration itself is then read
through that tenant's own registry.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import Depends, Request

from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.attributes import CALLER, annotate
from blizzard.foundation.roles import collaborator, dto
from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.foundation.tokens import TokenHash
from blizzard.hub.api import problems
from blizzard.hub.api.bearer import presented_bearer
from blizzard.hub.api.problem_exceptions import ProblemFactory, ProblemHTTPException
from blizzard.hub.api.resolution import unavailable_service
from blizzard.hub.auth.credential_reads import MachineCredentialReads
from blizzard.hub.domain.observability.tracing.attributes import RUNNER_ID, RUNNER_NAME
from blizzard.hub.domain.runners.host import Host, IReadHostRegistry
from blizzard.hub.domain.runners.registration import (
    IReadRunnerRegistry,
    RunnerRegistration,
    RunnerTokenRefused,
    refuse_runner_token,
)
from blizzard.hub.tenancy import STORE_UNAVAILABLE, TenantClosed, TenantId, UnknownTenant

_log = get_logger("blizzard.hub.auth")

#: ASGI-scope key holding the principal the trace gate already resolved, so the route's own auth
#: dependency reuses it rather than resolving the same token a second time.
_PRINCIPAL_SCOPE_KEY = "blizzard.runner_principal"

#: ASGI-scope key holding the registration that principal was resolved from, so the fleet gate asks the
#: loaded model whether it is retired rather than reading the registry a second time.
_REGISTRATION_SCOPE_KEY = "blizzard.runner_registration"

#: ASGI-scope key holding the host principal the request's bearer token already resolved to.
_HOST_PRINCIPAL_SCOPE_KEY = "blizzard.host_principal"

#: ASGI-scope key holding the host that principal was resolved from, so the gate asks the loaded model whether it is
#: retired rather than reading the registry a second time.
_HOST_SCOPE_KEY = "blizzard.host"

#: Why a fleet call is refused when its bearer token names no runner the hub can serve.
UNRESOLVED_RUNNER_TOKEN = "bearer token does not resolve to a known runner"

#: Why a host call is refused when its bearer token names no host the hub can serve.
UNRESOLVED_HOST_TOKEN = "bearer token does not resolve to a known host"


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


def resolved_runner(request: Request) -> RunnerPrincipal | None:
    """The runner ``request``'s bearer token already resolved to, or ``None`` when none was resolved — read
    without resolving the token again, so a tenant closed since still names the runner that called."""
    return request.scope.get(_PRINCIPAL_SCOPE_KEY)


class TenantRunnerRegistries:
    """Opens the canonical runner read registry of a tenant."""

    def __init__(self) -> None:
        self._open: Callable[[TenantId], IReadRunnerRegistry] | None = None

    def bind(self, open: Callable[[TenantId], IReadRunnerRegistry]) -> None:
        self._open = open

    def __call__(self, tenant: TenantId) -> IReadRunnerRegistry:
        assert self._open is not None
        return self._open(tenant)


class TenantHostRegistries:
    """Opens the canonical host read registry of a tenant."""

    def __init__(self) -> None:
        self._open: Callable[[TenantId], IReadHostRegistry] | None = None

    def bind(self, open: Callable[[TenantId], IReadHostRegistry]) -> None:
        self._open = open

    def __call__(self, tenant: TenantId) -> IReadHostRegistry:
        assert self._open is not None
        return self._open(tenant)


@collaborator
@dataclass(frozen=True)
class MachineCredentials:
    """Hub credential reads and the two typed tenant registry accessors."""

    reads: MachineCredentialReads | None
    runners: TenantRunnerRegistries
    hosts: TenantHostRegistries

    @property
    def credentials(self):
        """The hub-scoped credential reads, or the store's unavailability."""
        return self._require_reads()

    def _require_reads(self) -> MachineCredentialReads:
        if self.reads is None:
            unavailable_service(STORE_UNAVAILABLE)
        return self.reads

    def runner(self, tenant: TenantId, runner_id: str) -> RunnerRegistration | None:
        try:
            return self.runners(tenant).get_runner(runner_id)
        except (UnknownTenant, TenantClosed):
            return None

    def host(self, tenant: TenantId, host_id: str) -> Host | None:
        try:
            return self.hosts(tenant).get_host(host_id)
        except (UnknownTenant, TenantClosed):
            return None


@inject_sync
def machine_credentials(
    reads: FromDishka[MachineCredentialReads | None],
    runners: FromDishka[TenantRunnerRegistries],
    hosts: FromDishka[TenantHostRegistries],
) -> MachineCredentials:
    return MachineCredentials(reads, runners, hosts)


@dataclass(frozen=True)
class RunnerAuth:
    """One request's runner-bearer resolution and decisions."""

    request: Request
    machine: MachineCredentials

    @classmethod
    def of(cls, request: Request, machine: MachineCredentials) -> RunnerAuth:
        return cls(request, machine)

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
        resolved = self.machine.credentials.runner_for_token_hash(TokenHash(token).hex)
        if resolved is None:
            return None
        registration = self.machine.runner(resolved.tenant, resolved.runner_id)
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
        if token is not None and self.machine.credentials.revoked_token_runner(TokenHash(token).hex) is not None:
            _log.warning("revoked runner token presented", path=self.request.url.path)
            raise ProblemHTTPException(problems.authentication_revoked_problem)
        problem: ProblemFactory
        if token is None:
            reason = "missing or malformed Authorization header"
            problem = partial(problems.authentication_required_problem, detail=reason)
        else:
            reason = UNRESOLVED_RUNNER_TOKEN
            problem = partial(problems.authentication_unresolved_problem, detail=reason)
        _log.warning("runner auth failed", reason=reason, path=self.request.url.path)
        raise ProblemHTTPException(problem)

    def identify(self) -> RunnerRegistration:
        """The runner the presented token names, or :class:`RunnerTokenRefused` saying why the hub
        refuses it — ``missing`` with no token, else the verdict of :func:`refuse_runner_token` over
        the registration holding the token's hash and, when none does, the runner a revoked hash was
        issued to. Reads only."""
        token = presented_bearer(self.request)
        if token is None:
            raise RunnerTokenRefused(RunnerTokenRefusalReason.MISSING)
        token_hash = TokenHash(token).hex
        credentials = self.machine.credentials
        resolved = credentials.runner_for_token_hash(token_hash)
        current = None
        if resolved is not None:
            current = self.machine.runner(resolved.tenant, resolved.runner_id)
        revoked_for = None
        if current is None and (revoked := credentials.revoked_token_runner(token_hash)) is not None:
            revoked_for = self.machine.runner(revoked.tenant, revoked.runner_id)
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
        raise ProblemHTTPException(partial(problems.runner_credential_forbidden_problem, principal.runner_id))


@dto
@dataclass(frozen=True)
class HostPrincipal:
    """A bearer token resolved to the host it belongs to: its id, the name it holds, and the tenant it was added in,
    which is the tenant of every call it makes."""

    host_id: str
    host_name: str
    tenant_id: TenantId


@dataclass(frozen=True)
class HostAuth:
    """One request's host-bearer decision — the host router demands a principal; a runner's token resolves no host."""

    request: Request
    machine: MachineCredentials

    @classmethod
    def of(cls, request: Request, machine: MachineCredentials) -> HostAuth:
        return cls(request, machine)

    @property
    def principal(self) -> HostPrincipal | None:  # ast-grep-ignore: bzh:property-delegates
        """The presented token resolved to its host, or ``None`` when the header is missing/malformed or the token
        does not resolve — no rejection."""
        scope = self.request.scope
        if _HOST_PRINCIPAL_SCOPE_KEY not in scope:
            scope[_HOST_PRINCIPAL_SCOPE_KEY] = self._resolve()
        return scope[_HOST_PRINCIPAL_SCOPE_KEY]

    def _resolve(self) -> HostPrincipal | None:
        token = presented_bearer(self.request)
        if token is None:
            return None
        resolved = self.machine.credentials.host_for_token_hash(TokenHash(token).hex)
        if resolved is None:
            return None
        host = self.machine.host(resolved.tenant, resolved.host_id)
        if host is None:
            return None
        self.request.scope[_HOST_SCOPE_KEY] = host
        return HostPrincipal(host_id=host.host_id, host_name=host.name, tenant_id=resolved.tenant)

    def demand(self) -> HostPrincipal:
        """The resolved principal; a missing/malformed header, a revoked token, or one that resolves to no host raises
        401 — there is no tokenless host call. A principal whose host is retired raises
        :class:`~blizzard.hub.domain.runners.host.HostRetired` (403)."""
        principal = self.principal
        if principal is not None:
            host = self.request.scope.get(_HOST_SCOPE_KEY)
            if host is not None:
                host.refuse_if_retired(action="host call")
            return principal
        token = presented_bearer(self.request)
        if token is not None and self.machine.credentials.revoked_token_host(TokenHash(token).hex) is not None:
            _log.warning("revoked host token presented", path=self.request.url.path)
            raise ProblemHTTPException(problems.authentication_revoked_problem)
        problem: ProblemFactory
        if token is None:
            reason = "missing or malformed Authorization header"
            problem = partial(problems.authentication_required_problem, detail=reason)
        else:
            reason = UNRESOLVED_HOST_TOKEN
            problem = partial(problems.authentication_unresolved_problem, detail=reason)
        _log.warning("host auth failed", reason=reason, path=self.request.url.path)
        raise ProblemHTTPException(problem)

    def host(self) -> Host:
        """The loaded host the demanded principal names — read once by the resolution."""
        host = self.request.scope.get(_HOST_SCOPE_KEY)
        assert host is not None  # demand() resolved a principal first
        return host


@dto
@dataclass(frozen=True)
class RunnerSigningIn:
    """The runner a sign-in names, read in the tenant it was added in."""

    tenant: TenantId
    registration: RunnerRegistration


def runner_signing_in(machine: MachineCredentials, client: str) -> RunnerSigningIn | None:
    """The runner a sign-in names as its ``client``, read in the runner's own tenant, or ``None``
    when no open tenant holds a runner of that id. A runner id is hub-minted, so the client alone
    settles the tenant."""
    resolved = machine.credentials.runner_by_id(client)
    if resolved is None:
        return None
    registration = machine.runner(resolved.tenant, resolved.runner_id)
    return None if registration is None else RunnerSigningIn(resolved.tenant, registration)


@dto
@dataclass(frozen=True)
class HostSigningIn:
    """The host a sign-in names, read in the tenant it was added in."""

    tenant: TenantId
    host: Host


def host_signing_in(machine: MachineCredentials, client: str) -> HostSigningIn | None:
    """The host a sign-in names as its ``client``, read in the host's own tenant, or ``None`` when no open tenant
    holds a host of that id. A host id is hub-minted, so the client alone settles the tenant."""
    resolved = machine.credentials.host_by_id(client)
    if resolved is None:
        return None
    host = machine.host(resolved.tenant, resolved.host_id)
    return None if host is None else HostSigningIn(resolved.tenant, host)


def require_runner_principal(
    request: Request, machine: Annotated[MachineCredentials, Depends(machine_credentials)]
) -> RunnerPrincipal:
    return RunnerAuth.of(request, machine).demand()


def require_runner_principal_even_if_retired(
    request: Request, machine: Annotated[MachineCredentials, Depends(machine_credentials)]
) -> RunnerPrincipal:
    """The runner principal without the retirement refusal: a retired runner's token still resolves."""
    return RunnerAuth.of(request, machine).demand(allow_retired=True)


def reject_runner_principal(
    request: Request, machine: Annotated[MachineCredentials, Depends(machine_credentials)]
) -> None:
    RunnerAuth.of(request, machine).refuse_runner()


def require_host_principal(
    request: Request, machine: Annotated[MachineCredentials, Depends(machine_credentials)]
) -> HostPrincipal:
    return HostAuth.of(request, machine).demand()
