"""An HTTP refusal that names its problem variant, and the handler rendering it.

The producer names the variant, so the handler never infers it from the refusal's wording. An
operation declaring the variant at its status answers the problem; every other operation answers
FastAPI's ``{"detail": ...}`` body, carrying the problem's detail.
"""

from collections.abc import Mapping
from typing import Any, Protocol

from fastapi import HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from blizzard.hub.api.problem_rendering import declared_problem_response
from blizzard.wire.problem import CommonProblemMembers


class ProblemFactory(Protocol):
    def __call__(self, *, instance: str) -> CommonProblemMembers[Any, Any]: ...


class ProblemHTTPException(HTTPException):
    """An :class:`HTTPException` whose status and detail are the problem's own."""

    def __init__(self, problem: ProblemFactory, *, headers: Mapping[str, str] | None = None) -> None:
        body = problem(instance="/").problem_dump()
        status = body["status"]
        if not isinstance(status, int):
            raise TypeError("a refused request's problem must carry an integer status")
        super().__init__(status_code=status, detail=body["detail"], headers=dict(headers) if headers else None)
        self.problem = problem


async def problem_http_exception(request: Request, exc: Exception) -> Response:
    """Leave undeclared and untyped refusals to FastAPI's original handler."""
    assert isinstance(exc, StarletteHTTPException)
    if isinstance(exc, ProblemHTTPException):
        declared = declared_problem_response(request, exc.problem(instance=request.url.path), headers=exc.headers)
        if declared is not None:
            return declared
    return await http_exception_handler(request, exc)
