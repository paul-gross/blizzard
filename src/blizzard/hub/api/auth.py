"""Runner-bearer-token authentication at the hub's edge (issue #86a).

A presented token resolves by sha256-hex-digest lookup against the stored hash column;
that selection **is** the match, so no separate ``hmac.compare_digest`` is load-bearing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from blizzard.foundation.logging import get_logger
from blizzard.hub.api.bearer import presented_bearer
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.config import RUNNER_AUTH_ENFORCE
from blizzard.hub.domain.enrollment import TokenHash

_log = get_logger("blizzard.hub.auth")


@dataclass(frozen=True)
class RunnerPrincipal:
    """A bearer token resolved to the runner it belongs to."""

    runner_id: str
    workspace_id: str


@dataclass(frozen=True)
class AuthMode:
    """The runner-auth rollout brake — the one place a refusal decides raise vs. log."""

    value: str

    @classmethod
    def of(cls, request: Request) -> AuthMode:
        return cls(request.app.state.config.runner_auth_mode)

    @property
    def enforcing(self) -> bool:
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
    def principal(self) -> RunnerPrincipal | None:
        """The presented token resolved to its runner, or ``None`` when the header is
        missing/malformed or the token does not resolve — no mode logic, no rejection."""
        token = presented_bearer(self.request)
        if token is None:
            return None
        registration = self.services.registry.registration_for_token_hash(TokenHash(token).hex)
        if registration is None:
            return None
        return RunnerPrincipal(runner_id=registration.runner_id, workspace_id=registration.workspace_id)

    def demand(self) -> RunnerPrincipal | None:
        """The resolved principal, or ``None`` under ``warn``. Under ``enforce`` a
        missing/malformed header or an unresolved token raises 401."""
        principal = self.principal
        if principal is not None:
            return principal
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
        """Refuse a runner's token on an operator router — valid only on the fleet router
        (issue #87). An unresolvable token is not flagged: that is what an anonymous
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
