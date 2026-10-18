"""What a flag hides while it is off: the route guard on both daemons, the wire-field marker, and the ``404`` body a
held route serves against the one its OpenAPI entry declares (``bzh:flag-hides-behavior``)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from blizzard.foundation.flags import (
    FlagGuarded,
    FlagOffError,
    FlagRegistry,
    Flags,
    resolve_flags,
    without_off_fields,
)
from blizzard.hub.api.flag_guard import flag_guard as hub_flag_guard
from blizzard.hub.app import create_app as create_hub_app
from blizzard.hub.config import HubConfig
from blizzard.runner.api.flag_guard import flag_guard as runner_flag_guard
from blizzard.runner.app import create_app as create_runner_app
from blizzard.runner.config import RunnerConfig
from blizzard.wire.flags import FlagOff

_REGISTRY = """
[flag.hub-thing]
owner = "epic:release-engineering/dark-launch"
summary = "A hub capability"
scope = "hub"
default = "off"

[flag.runner-thing]
owner = "initiative:a-runner-thing"
summary = "A runner capability"
scope = "runner"
default = "off"
"""


def _flags(side: str, **overrides: bool) -> Flags:
    return resolve_flags(FlagRegistry.parse(_REGISTRY, "fixture"), side, overrides).flags  # type: ignore[arg-type]


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


@pytest.mark.component
@pytest.mark.parametrize(("build", "side", "flag"), [(_hub, "hub", "hub-thing"), (_host, "runner", "runner-thing")])
def test_a_held_route_is_declared_answers_404_with_the_flag_while_off_and_answers_once_on(
    build: Any, side: str, flag: str
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


@pytest.mark.unit
def test_require_on_raises_for_an_off_flag_and_checks_the_name_as_on_does() -> None:
    flags = _flags("hub")
    with pytest.raises(FlagOffError) as raised:
        flags.require_on("hub-thing")
    assert raised.value.flag == "hub-thing"
    flags_on = _flags("hub", **{"hub-thing": True})
    flags_on.require_on("hub-thing")
    with pytest.raises(LookupError):
        flags_on.require_on("mistyped")


class _Inner(BaseModel):
    note: Annotated[str, FlagGuarded("hub-thing")] = "plain"


class _Wire(BaseModel):
    kept: int = 1
    hidden: Annotated[int, FlagGuarded("hub-thing")] = 0
    listed: Annotated[list[str], FlagGuarded("hub-thing")] = []
    inner: _Inner = _Inner()
    inners: list[_Inner] = []


@pytest.mark.unit
def test_a_field_behind_an_off_flag_is_accepted_and_ignored_on_a_request_and_default_on_a_response() -> None:
    sent = _Wire.model_validate(
        {"kept": 5, "hidden": 9, "listed": ["x"], "inner": {"note": "n"}, "inners": [{"note": "m"}]}
    )
    shed = without_off_fields(sent, _flags("hub"))
    assert shed == _Wire(kept=5, inners=[_Inner(note="plain")]) and shed.inner.note == "plain"
    assert sent.hidden == 9


@pytest.mark.unit
def test_a_field_behind_an_on_flag_passes_through_untouched() -> None:
    sent = _Wire(kept=5, hidden=9, listed=["x"])
    assert without_off_fields(sent, _flags("hub", **{"hub-thing": True})) is sent


@pytest.mark.unit
def test_a_guarded_field_without_a_default_is_a_declaration_error() -> None:
    class Bad(BaseModel):
        required: Annotated[int, FlagGuarded("hub-thing")]

    with pytest.raises(TypeError, match="needs a default"):
        without_off_fields(Bad(required=1), _flags("hub"))
