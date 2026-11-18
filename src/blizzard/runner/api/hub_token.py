"""``PUT /api/host/runners/{runner_id}/token`` — replace one of the host's runners' hub token, sealed in the host store.

Write-only: the value is never echoed, logged or recorded. The running runner takes the replaced token up at its next
identity check."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, SecretStr

from blizzard.foundation.secrets.value import SecretValueBlank
from blizzard.runner.api.federation import require_superuser
from blizzard.runner.api.problems import problem, reject_worker_credential
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.auth.session import RunnerSession
from blizzard.runner.hub.sealed_tokens import HubTokenRetired

router = APIRouter(prefix="/api/host", tags=["host"], dependencies=[Depends(reject_worker_credential)])


class HubTokenWrite(BaseModel):
    """The runner's new hub bearer token."""

    token: SecretStr


_HUB_TOKEN_PROBLEM = {"application/problem+json": {"schema": {"$ref": "#/components/schemas/RunnerProblem"}}}


@router.put(
    "/runners/{runner_id}/token",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        403: {"description": "Superuser required"},
        404: {"description": "No runner of this host has the id", "content": _HUB_TOKEN_PROBLEM},
        409: {"description": "The hub refused the runner's token as retired", "content": _HUB_TOKEN_PROBLEM},
        422: {"description": "A blank token", "content": _HUB_TOKEN_PROBLEM},
    },
)
def set_runner_token(
    request: Request,
    runner_id: str,
    body: HubTokenWrite,
    _session: Annotated[RunnerSession, Depends(require_superuser)],
) -> Response:
    """Seal ``token`` as the runner's hub token at its row's next revision. Any runner a ``[[runner]]`` of this host
    names counts, halted or served, so a halted runner's token can be set for its next start."""
    wiring = RunnerWiring.of(request)
    registry = wiring.runners()
    served = next((runner for runner in registry.served if runner.runner_id == runner_id), None)
    if served is None and not any(halted.id == runner_id for halted in registry.halted):
        return problem(404, "runner_unknown", f"this host holds no runner {runner_id}")
    verdict = wiring.for_runner(served).maybe_identity_verdict() if served is not None else None
    try:
        wiring.hub_tokens().replace(runner_id, body.token.get_secret_value(), verdict=verdict)
    except SecretValueBlank as exc:
        return problem(422, "token_blank", str(exc))
    except HubTokenRetired as exc:
        return problem(409, "runner_retired", str(exc))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
