"""The host's route guard: one declaration holds a route behind a flag (``bzh:flag-hides-behavior``).

``@router.get("/x", **flag_guard("name"))`` adds the dependency that raises while the flag is off and the ``404`` the
route's OpenAPI entry declares; the app's one handler serves it as a :class:`~blizzard.wire.flags.FlagOff`."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Request, status
from fastapi.responses import JSONResponse

from blizzard.foundation.flags import FlagOffError
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.wire.flags import FlagOff, FlagOffResponse


def flag_guard(name: str) -> dict[str, Any]:
    """The route keyword arguments that hold a route behind the runner flag ``name``: its dependency and its ``404``."""

    def hold(request: Request) -> None:
        RunnerWiring.of(request).flags().require_on(name)

    return {
        "dependencies": [Depends(hold)],
        "responses": {status.HTTP_404_NOT_FOUND: {"model": FlagOffResponse}},
    }


def refuse_flag_off(request: Request, exc: Exception) -> JSONResponse:
    """Answer a held route while its flag is off: ``404`` with the code ``flag_off`` naming the flag."""
    assert isinstance(exc, FlagOffError)
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": FlagOff.of(exc.flag).model_dump()})
