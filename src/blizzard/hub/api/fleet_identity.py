"""The runner identity route — how a runner learns whom its bearer token names, or why the hub
refuses it.

The one ``/api/fleet`` path outside the fleet router's runner-principal gate: the gate refuses an
unresolved token before any handler runs, so a route behind it could never answer the refusal.
This route reads the token itself and answers every outcome, 200 or a typed 401."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.hub.api.auth import RunnerAuth, RunnerPrincipal
from blizzard.hub.api.hub_level import Hub
from blizzard.hub.domain.runners.registration import RunnerTokenRefused
from blizzard.wire.runner import RunnerIdentityRefusal, RunnerIdentityView

router = APIRouter(prefix="/api/fleet", tags=["fleet"])


@router.get(
    "/identity",
    response_model=RunnerIdentityView,
    responses={status.HTTP_401_UNAUTHORIZED: {"model": RunnerIdentityRefusal}},
)
def get_runner_identity(request: Request, services: Hub) -> RunnerIdentityView | JSONResponse:
    """The id and name of the runner the presented bearer token names, and the tenant it was added
    in, or a 401 saying why the hub refuses it: ``missing``, ``unknown``, ``revoked`` or
    ``retired``, the last two naming the runner the token was issued to. Registers nothing and
    records no liveness."""
    try:
        identified = _identified(RunnerAuth.of(request, services))
    except RunnerTokenRefused as exc:
        refusal = RunnerIdentityRefusal(reason=exc.reason, runner_id=exc.runner_id)
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content=refusal.model_dump(mode="json"))
    return RunnerIdentityView(
        runner_id=identified.runner_id,
        runner_name=identified.runner_name,
        tenant_id=identified.tenant_id,
        tenant_name=services.tenants.open_tenants.name(identified.tenant_id),
        fleet_flags=request.app.state.flags.fleet_answer(),
    )


def _identified(auth: RunnerAuth) -> RunnerPrincipal:
    """The runner the token names, with the tenant it was added in, once :meth:`RunnerAuth.identify`
    admits the token; the principal is resolved from the same token. A token that stops resolving
    between the two reads is refused as ``unknown``."""
    auth.identify()
    principal = auth.principal
    if principal is None:
        raise RunnerTokenRefused(RunnerTokenRefusalReason.UNKNOWN)
    return principal
