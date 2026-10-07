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
from blizzard.foundation.roles import dto
from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.foundation.tokens import TokenHash
from blizzard.hub.api.bearer import presented_bearer
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.observability.tracing.attributes import RUNNER_ID, RUNNER_NAME
from blizzard.hub.domain.runners.registration import RunnerRegistration, RunnerTokenRefused, refuse_runner_token

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
    """A bearer token resolved to the runner it belongs to: its id, the name it holds, and its
    workspace — ``None`` until the runner first registers."""

    runner_id: str
    runner_name: str
    workspace_id: str | None


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
        registration = self.services.registry.registration_for_token_hash(TokenHash(token).hex)
        if registration is None:
            return None
        self.request.scope[_REGISTRATION_SCOPE_KEY] = registration
        return RunnerPrincipal(
            runner_id=registration.runner_id, runner_name=registration.name, workspace_id=registration.workspace_id
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
        if token is not None and self.services.registry.is_token_revoked(TokenHash(token).hex):
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
        registry = self.services.registry
        current = registry.registration_for_token_hash(token_hash)
        revoked_runner_id = registry.revoked_token_runner_id(token_hash) if current is None else None
        revoked_for = registry.get_runner(revoked_runner_id) if revoked_runner_id is not None else None
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


def require_runner_principal(
    request: Request, services: Annotated[HubServices, Depends(get_services)]
) -> RunnerPrincipal:
    return RunnerAuth.of(request, services).demand()


def require_runner_principal_even_if_retired(
    request: Request, services: Annotated[HubServices, Depends(get_services)]
) -> RunnerPrincipal:
    """The principal without the retirement refusal — the claim route's, whose retired refusal is the
    paused-denial body a runner parses (``ClaimService`` raises it in-domain)."""
    return RunnerAuth.of(request, services).demand(allow_retired=True)


def reject_runner_principal(request: Request, services: Annotated[HubServices, Depends(get_services)]) -> None:
    RunnerAuth.of(request, services).refuse_runner()
