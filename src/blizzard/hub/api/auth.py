"""Runner-bearer-token authentication at the hub's edge.

A presented token resolves by sha256-hex-digest lookup against the stored hash column;
that selection **is** the match, so no separate ``hmac.compare_digest`` is load-bearing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.attributes import CALLER, annotate
from blizzard.foundation.roles import domain_model, dto
from blizzard.foundation.tokens import TokenHash
from blizzard.hub.api.bearer import presented_bearer
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.config import RUNNER_AUTH_ENFORCE
from blizzard.hub.domain.tracing.attributes import RUNNER_ID

_log = get_logger("blizzard.hub.auth")

#: ASGI-scope key holding the principal the trace gate already resolved, so the route's own auth
#: dependency reuses it rather than resolving the same token a second time.
_PRINCIPAL_SCOPE_KEY = "blizzard.runner_principal"


@dto
@dataclass(frozen=True)
class RunnerPrincipal:
    """A bearer token resolved to the runner it belongs to."""

    runner_id: str
    workspace_id: str


@domain_model
@dataclass(frozen=True)
class AuthMode:
    """The runner-auth rollout brake — the one place a refusal decides raise vs. log."""

    value: str

    @classmethod
    def of(cls, request: Request) -> AuthMode:
        return cls(request.app.state.config.runner_auth_mode)

    @property
    def enforcing(self) -> bool:  # ast-grep-ignore: bzh:property-delegates
        return self.value == RUNNER_AUTH_ENFORCE

    def refuse(self, *, status_code: int, detail: str, event: str, **fields: object) -> None:
        if self.enforcing:
            raise HTTPException(status_code=status_code, detail=detail)
        _log.warning(event, **fields)


@dataclass(frozen=True)
class RunnerAuth:
    """One request's runner-bearer decision — resolution stays separate from what each
    router does with it: the fleet router demands a principal, the operator routers refuse one."""

    request: Request
    services: HubServices
    mode: AuthMode

    @classmethod
    def of(cls, request: Request, services: HubServices) -> RunnerAuth:
        return cls(request, services, AuthMode.of(request))

    @property
    def principal(self) -> RunnerPrincipal | None:  # ast-grep-ignore: bzh:property-delegates
        """The presented token resolved to its runner, or ``None`` when the header is
        missing/malformed or the token does not resolve — no mode logic, no rejection."""
        scope = self.request.scope
        if _PRINCIPAL_SCOPE_KEY not in scope:
            scope[_PRINCIPAL_SCOPE_KEY] = self._resolve()
        return scope[_PRINCIPAL_SCOPE_KEY]

    def _resolve(self) -> RunnerPrincipal | None:
        token = presented_bearer(self.request)
        if token is None:
            return None
        registration = self.services.registry.registration_for_token_hash(TokenHash(token).hex)
        if registration is None:
            return None
        return RunnerPrincipal(runner_id=registration.runner_id, workspace_id=registration.workspace_id)

    def demand(self) -> RunnerPrincipal | None:
        """The resolved principal, or ``None`` under ``warn``. Under ``enforce`` a
        missing/malformed header or an unresolved token raises 401. A **revoked** token
        raises 401 under every mode — checked before :meth:`AuthMode.refuse` is consulted,
        since ``warn`` would otherwise let it through as anonymous."""
        principal = self.principal
        if principal is not None:
            annotate({CALLER: "runner", RUNNER_ID: principal.runner_id})
            return principal
        token = presented_bearer(self.request)
        if token is not None and self.services.registry.is_token_revoked(TokenHash(token).hex):
            _log.warning("revoked runner token presented", path=self.request.url.path)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bearer token has been revoked")
        reason = (
            "missing or malformed Authorization header"
            if presented_bearer(self.request) is None
            else "bearer token does not resolve to a known runner"
        )
        self.mode.refuse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=reason,
            event="runner auth failed",
            reason=reason,
            path=self.request.url.path,
        )
        return None

    def refuse_runner(self) -> None:
        """Refuse a runner's token on an operator router — valid only on the fleet router.
        An unresolvable token is not flagged: that is what an anonymous
        operator call looks like."""
        principal = self.principal
        if principal is None:
            return
        self.mode.refuse(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"runner token for {principal.runner_id!r} is not valid on an operator verb",
            event="runner token presented on operator verb",
            runner_id=principal.runner_id,
            path=self.request.url.path,
        )


def require_runner_principal(
    request: Request, services: Annotated[HubServices, Depends(get_services)]
) -> RunnerPrincipal | None:
    return RunnerAuth.of(request, services).demand()


def reject_runner_principal(request: Request, services: Annotated[HubServices, Depends(get_services)]) -> None:
    RunnerAuth.of(request, services).refuse_runner()
