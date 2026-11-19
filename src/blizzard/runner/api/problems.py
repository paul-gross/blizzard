"""The problem body a runner route refuses with, and the dependency that refuses a worker's lease token on a route a
runner session owns."""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from blizzard.runner.api.lease_token import presented_lease_token
from blizzard.wire.base import WireModel


def reject_worker_credential(request: Request) -> None:
    # An ungated host grants implicit human authority, but a presented lease token
    # identifies the worker lane, not an implicit human caller.
    if presented_lease_token(request) is not None:
        raise HTTPException(status_code=401, detail="runner session required")


class RunnerProblem(WireModel):
    type: str
    title: str
    status: int
    detail: str
    code: str


def problem(
    status: int, code: str, message: str, *, model: type[RunnerProblem] = RunnerProblem, **members: object
) -> JSONResponse:
    """A ``model`` problem response for ``code``, carrying ``members`` beside the common ones."""
    return JSONResponse(
        status_code=status,
        content=model.model_validate(
            {
                "type": f"urn:blizzard:problem:{code.replace('_', '-')}",
                "title": code.replace("_", " "),
                "status": status,
                "detail": message,
                "code": code,
                **members,
            }
        ).model_dump(mode="json"),
        media_type="application/problem+json",
    )
