"""What a flag hides while it is off, on both daemons: the route guard and the ``404`` body a held route serves
against the one its OpenAPI entry declares (``bzh:flag-hides-behavior``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from blizzard.foundation.flags import Flags, FlagSide
from blizzard.hub.api.flag_guard import flag_guard as hub_flag_guard
from blizzard.hub.config import HubConfig
from blizzard.runner.api.flag_guard import flag_guard as runner_flag_guard
from blizzard.runner.config import RunnerConfig
from blizzard.wire.flags import FlagOff
from tests.support.config_inputs import create_hub_app, create_runner_app
from tests.support.flag_guard import flags_for as _flags


def _router(guard: Any, name: str) -> APIRouter:
    router = APIRouter()

    @router.get("/api/held", **guard(name))
    def held() -> dict[str, str]:
        return {"held": "yes"}

    return router


def _mount(app: FastAPI, router: APIRouter) -> FastAPI:
    """Mount ``router`` ahead of the catch-all web mount, which would otherwise answer its path."""
    before = len(app.router.routes)
    app.include_router(router)
    added = app.router.routes[before:]
    app.router.routes[:] = [*added, *app.router.routes[:before]]
    return app


def _hub(flags: Flags) -> FastAPI:
    app = create_hub_app(HubConfig(root=Path("."), db_url="sqlite://"), flags=flags)
    return _mount(app, _router(hub_flag_guard, "hub-thing"))


def _host(flags: Flags) -> FastAPI:
    app = create_runner_app(RunnerConfig(root=Path("."), db_url="sqlite://"), flags=flags)
    return _mount(app, _router(runner_flag_guard, "runner-thing"))


def _declared_404(app: FastAPI) -> dict[str, Any]:
    document = app.openapi()
    schema = document["paths"]["/api/held"]["get"]["responses"]["404"]["content"]["application/json"]["schema"]
    response = document["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    return document["components"]["schemas"][response["properties"]["detail"]["$ref"].rsplit("/", 1)[-1]]


@pytest.mark.parametrize(("build", "side", "flag"), [(_hub, "hub", "hub-thing"), (_host, "runner", "runner-thing")])
def test_a_held_route_is_declared_answers_404_with_the_flag_while_off_and_answers_once_on(
    build: Any, side: FlagSide, flag: str
) -> None:
    off = build(_flags(side))
    declared = _declared_404(off)
    assert declared == _declared_404(build(_flags(side, **{flag: True})))
    served = TestClient(off).get("/api/held")
    assert served.status_code == 404
    body = served.json()["detail"]
    assert body == FlagOff.of(flag).model_dump()
    assert set(body) == set(declared["properties"])
    assert FlagOff.model_validate(body).flag == flag

    on = TestClient(build(_flags(side, **{flag: True}))).get("/api/held")
    assert (on.status_code, on.json()) == (200, {"held": "yes"})


@pytest.mark.parametrize(("build", "side"), [(_hub, "hub"), (_host, "runner")])
def test_a_guard_keeps_the_dependencies_and_responses_a_route_already_declares(build: Any, side: FlagSide) -> None:
    flag = "hub-thing" if side == "hub" else "runner-thing"
    guard = hub_flag_guard if side == "hub" else runner_flag_guard
    seen: list[str] = []

    def own() -> None:
        seen.append("own")

    router = APIRouter()

    @router.get("/api/own", **guard(flag, dependencies=[Depends(own)], responses={409: {"description": "Conflict"}}))
    def own_route() -> dict[str, str]:
        return {"held": "yes"}

    app = _mount(build(_flags(side, **{flag: True})), router)
    assert TestClient(app).get("/api/own").status_code == 200
    assert seen == ["own"]
    responses = app.openapi()["paths"]["/api/own"]["get"]["responses"]
    assert {"404", "409"} <= set(responses)
