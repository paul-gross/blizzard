"""``PUT /api/host/runners/{runner_id}/token`` — replace one of the host's runners' hub token, sealed in the host store.

Write-only: the value is never echoed, logged or recorded. The running runner takes the replaced token up at its next
identity check."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, SecretStr

from blizzard.foundation.secrets.value import SecretValueBlank
from blizzard.runner.api.federation import require_superuser
from blizzard.runner.api.lease_token import presented_lease_token
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.auth.session import RunnerSession
from blizzard.runner.hub.sealed_tokens import HubTokenRetired
from blizzard.wire.base import WireModel


def _reject_worker_credential(request: Request) -> None:
    # A worker's lease token names the worker lane, never the implicit human an ungated host grants.
    if presented_lease_token(request) is not None:
        raise HTTPException(status_code=401, detail="runner session required")


router = APIRouter(prefix="/api/host", tags=["host"], dependencies=[Depends(_reject_worker_credential)])


class HubTokenWrite(BaseModel):
    """The runner's new hub bearer token."""

    token: SecretStr


class HubTokenProblem(WireModel):
    type: str
    title: str
    status: int
    detail: str
    code: str


_HUB_TOKEN_PROBLEM = {"application/problem+json": {"schema": {"$ref": "#/components/schemas/HubTokenProblem"}}}


def _problem(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=HubTokenProblem(
            type=f"urn:blizzard:problem:{code.replace('_', '-')}",
            title=code.replace("_", " "),
            status=status_code,
            detail=message,
            code=code,
        ).model_dump(mode="json"),
        media_type="application/problem+json",
    )


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
        return _problem(404, "runner_unknown", f"this host holds no runner {runner_id}")
    verdict = wiring.for_runner(served).maybe_identity_verdict() if served is not None else None
    try:
        wiring.hub_tokens().replace(
            runner_id, body.token.get_secret_value(), retired=verdict is not None and verdict.token_retired()
        )
    except SecretValueBlank as exc:
        return _problem(422, "token_blank", str(exc))
    except HubTokenRetired as exc:
        return _problem(409, "runner_retired", str(exc))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
