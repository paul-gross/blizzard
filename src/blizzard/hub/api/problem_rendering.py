"""Render a hub-authored problem model as an ``application/problem+json`` response.

A model carrying an integer ``status`` names its own HTTP status. A legacy body whose
``status`` is a string, or that omits it, takes the HTTP status from the caller.
"""

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

from blizzard.hub.api.problem_declarations import declared_problem_variants
from blizzard.wire.problem import CommonProblemMembers

PROBLEM_MEDIA_TYPE = "application/problem+json"


def _require_problem(problem: object) -> None:
    if not isinstance(problem, CommonProblemMembers):
        raise TypeError("problem must be CommonProblemMembers")


def _http_status(content: Mapping[str, object], status_code: int | None) -> int:
    carried = content.get("status")
    if isinstance(carried, int) and not isinstance(carried, bool):
        if status_code is not None:
            raise TypeError("the problem carries its own HTTP status; pass no status_code")
        return carried
    if status_code is None:
        raise TypeError("a problem without an integer status needs a status_code")
    return status_code


def problem_response(
    problem: CommonProblemMembers[Any, Any],
    *,
    status_code: int | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Render the wire model without inventing or dropping its fields."""
    _require_problem(problem)
    content = problem.problem_dump()
    path = urlsplit(problem.instance).path
    content["instance"] = path if path.startswith("/") and not path.startswith("//") else "/"
    return JSONResponse(
        content=content,
        status_code=_http_status(content, status_code),
        headers=headers,
        media_type=PROBLEM_MEDIA_TYPE,
    )


def declared_problem_response(
    request: Request, problem: CommonProblemMembers[Any, Any], *, headers: Mapping[str, str] | None = None
) -> JSONResponse | None:
    """The problem response when the request's operation declares this variant at its status, else ``None``."""
    response = problem_response(problem, headers=headers)
    if type(problem) in declared_problem_variants(request).get(response.status_code, ()):
        return response
    return None
