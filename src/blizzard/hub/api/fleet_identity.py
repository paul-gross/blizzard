"""The runner identity route — how a runner learns whom its bearer token names, or why the hub
refuses it.

The one ``/api/fleet`` path outside the fleet router's runner-principal gate: the gate refuses an
unresolved token before any handler runs, so a route behind it could never answer the refusal.
This route reads the token itself and answers every outcome, 200 or a typed 401."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from blizzard.hub.api.auth import RunnerAuth
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.runners.registration import RunnerTokenRefused
from blizzard.wire.runner import RunnerIdentityRefusal, RunnerIdentityView

router = APIRouter(prefix="/api/fleet", tags=["fleet"])


@router.get(
    "/identity",
    response_model=RunnerIdentityView,
    responses={status.HTTP_401_UNAUTHORIZED: {"model": RunnerIdentityRefusal}},
)
def get_runner_identity(
    request: Request, services: Annotated[HubServices, Depends(get_services)]
) -> RunnerIdentityView | JSONResponse:
    """The id and name of the runner the presented bearer token names, or a 401 saying why the hub
    refuses it: ``missing``, ``unknown``, ``revoked`` or ``retired``, the last two naming the runner
    the token was issued to. Registers nothing and records no liveness."""
    try:
        registration = RunnerAuth.of(request, services).identify()
    except RunnerTokenRefused as exc:
        refusal = RunnerIdentityRefusal(reason=exc.reason, runner_id=exc.runner_id)
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content=refusal.model_dump(mode="json"))
    return RunnerIdentityView(runner_id=registration.runner_id, runner_name=registration.name)
