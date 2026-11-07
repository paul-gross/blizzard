"""What a domain refusal answers with: a problem a client dispatches on by ``type``, or an ``HTTPException`` by status
for a refusal no client tells apart from the others at its status."""

# exemplar: bzh:domain-orchestration-split — a route maps each refusal it meets to its status explicitly.

from __future__ import annotations

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse

from blizzard.hub.api.problem_rendering import problem_response
from exemplar.hub.domain.permits.model import PermitAlreadyRequested
from exemplar.wire.permit import PermitAlreadyRequestedProblem


def already_requested(exc: PermitAlreadyRequested, request: Request) -> JSONResponse:
    """``409`` as ``application/problem+json``: a stable ``type``, the refusal's text as ``detail``, the request path
    as ``instance``. The model carries its own ``status``, so the response takes it from there."""
    return problem_response(
        PermitAlreadyRequestedProblem(
            type="urn:blizzard:problem:permit-already-requested",
            title="Permit already requested",
            detail=str(exc),
            instance=request.url.path,
            status=status.HTTP_409_CONFLICT,
        )
    )


def not_found(detail: str) -> HTTPException:
    """``404`` — the call names something the tenant does not hold."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def conflict(detail: str) -> HTTPException:
    """``409`` — the current state of what the call acts on refuses it."""
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def unprocessable(detail: str) -> HTTPException:
    """``422`` — the call's input is well-formed but refused."""
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)
