"""The hub's route guard: one declaration holds a route behind a flag (``bzh:flag-hides-behavior``).

``@router.get("/x", **flag_guard("name"))`` adds the dependency that raises while the flag is off and the ``404`` the
route's OpenAPI entry declares; the app's one handler serves it as a :class:`~blizzard.wire.flags.FlagOff`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import Depends, Request, status
from fastapi.responses import JSONResponse

from blizzard.foundation.flags import FlagOffError, Flags
from blizzard.hub.api.problem_rendering import problem_response
from blizzard.wire.flags import FlagOff, FlagOffProblem, FlagOffResponse
from blizzard.wire.problem import FLAG_OFF


def flag_guard(
    name: str, *, dependencies: Sequence[Any] = (), responses: Mapping[int | str, Any] | None = None
) -> dict[str, Any]:
    """The route keyword arguments that hold a route behind the hub flag ``name``, after the route's own ones."""

    def hold(request: Request) -> None:
        held: Flags = request.app.state.flags
        held.require_on(name)

    return {
        "dependencies": [*dependencies, Depends(hold)],
        "responses": {**(responses or {}), status.HTTP_404_NOT_FOUND: {"model": FlagOffResponse}},
    }


def refuse_flag_off(request: Request, exc: Exception) -> JSONResponse:
    """Answer a held route while its flag is off: a ``404`` problem whose ``detail`` is the
    ``flag_off`` object naming the flag."""
    assert isinstance(exc, FlagOffError)
    return problem_response(
        FlagOffProblem(
            type=FLAG_OFF,
            title="Not available yet",
            detail=FlagOff.of(exc.flag),
            instance=request.url.path,
            status=status.HTTP_404_NOT_FOUND,
        )
    )
