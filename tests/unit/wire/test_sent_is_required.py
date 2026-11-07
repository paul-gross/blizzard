"""The wire schemas' ``required`` lists (unit tier): in each exported spec, a response-only component
schema lists every property as required, because the server always sends it. The sets are derived from the
built apps and their specs, so a model added later without the wire base fails here (``bzh:wire-sent-is-required``)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, cast, get_args

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from blizzard.cli.config_inputs import packaged_flag_registry
from blizzard.hub.app import create_app_for_export as build_hub_app
from blizzard.runner.app import create_app_for_export as build_runner_app
from blizzard.wire.base import WireModel
from blizzard.wire.problem import CommonProblemMembers
from blizzard.wire.sse import SseFramePayload
from blizzard.wire.sse_runner import RunnerScopedPayload

_REF_PREFIX = "#/components/schemas/"

#: FastAPI's own 422 body shapes, which no blizzard model owns.
_FRAMEWORK_SCHEMAS = frozenset({"HTTPValidationError", "ValidationError"})


def _subclasses[T](cls: type[T]) -> Iterator[type[T]]:
    for sub in cls.__subclasses__():
        yield sub
        yield from _subclasses(sub)


def _refs(node: object) -> Iterator[str]:
    """Every component schema name ``$ref``-ed anywhere under ``node``."""
    if isinstance(node, dict):
        mapping = cast("dict[object, object]", node)
        for key, value in mapping.items():
            if key == "$ref" and isinstance(value, str) and value.startswith(_REF_PREFIX):
                yield value.removeprefix(_REF_PREFIX)
            else:
                yield from _refs(value)
    elif isinstance(node, list):
        items = cast("list[object]", node)
        for item in items:
            yield from _refs(item)


def _request_body(operation: object) -> object:
    if not isinstance(operation, dict):
        return {}
    mapping = cast("dict[object, object]", operation)
    return mapping.get("requestBody", {})


def _closure(roots: set[str], schemas: dict[str, dict[str, Any]]) -> set[str]:
    """``roots`` plus every schema they reach through ``$ref``s."""
    seen: set[str] = set()
    pending = list(roots)
    while pending:
        name = pending.pop()
        if name in seen or name not in schemas:
            continue
        seen.add(name)
        pending.extend(_refs(schemas[name]))
    return seen


def _request_reached(spec: dict[str, Any]) -> set[str]:
    """Schemas a request body reaches, and the ``-Input``/``-Output`` pairs a schema already split into —
    the split is the spec saying some request parses it."""
    schemas = spec["components"]["schemas"]
    split = {name for name in schemas if name.endswith("-Input")}
    bodies = (
        split
        | {name.removesuffix("-Input") + "-Output" for name in split}
        | {
            name
            for operations in spec["paths"].values()
            for operation in operations.values()
            for name in _refs(_request_body(operation))
        }
    )
    return _closure(bodies, schemas)


def _api_routes(routes: Iterable[object]) -> Iterator[APIRoute]:
    """Every route under ``routes``, through the lazily included routers and mounts."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        nested = getattr(getattr(route, "original_router", route), "routes", None)
        if nested:
            yield from _api_routes(nested)


def _named_classes(annotation: object) -> Iterator[type]:
    """Every class an annotation names, looking through ``list[X]``, ``X | None`` and similar shapes."""
    if isinstance(annotation, type) and not get_args(annotation):
        yield annotation
    for arg in get_args(annotation):
        yield from _named_classes(arg)


def _exclude_flagged(app: FastAPI, spec: dict[str, Any]) -> set[str]:
    """Schemas served by a route that drops fields from its reply (``response_model_exclude_*``)."""
    flagged = {
        cls.__name__
        for route in _api_routes(app.routes)
        if (
            route.response_model_exclude_none
            or route.response_model_exclude_unset
            or route.response_model_exclude_defaults
        )
        for cls in _named_classes(route.response_model)
    }
    return _closure(flagged, spec["components"]["schemas"])


def _sse_payloads() -> set[str]:
    return {cls.__name__ for cls in (*_subclasses(SseFramePayload), SseFramePayload, RunnerScopedPayload)}


def _optional_properties(schema_name: str, schema: dict[str, Any], partial_status: set[str]) -> set[str]:
    optional = set(schema.get("properties", {})) - set(schema.get("required", []))
    if schema_name in partial_status:
        optional.discard("status")
    return optional


def _apps() -> list[tuple[str, FastAPI]]:
    registry = packaged_flag_registry()
    return [("hub", build_hub_app(registry)), ("runner", build_runner_app(registry))]


def _request_reached_anywhere() -> set[str]:
    """Schemas a request reaches in either spec. A model shared across both keeps its shape in each: a required
    list on the spec that only serves it would split the schema in the one that also parses it."""
    reached = {name for _, app in _apps() for name in _request_reached(app.openapi())}
    return reached | {name.removesuffix("-Input").removesuffix("-Output") for name in reached}


@pytest.mark.parametrize(("name", "app"), _apps(), ids=lambda value: value if isinstance(value, str) else "")
def test_a_response_only_schema_lists_every_property_as_required(name: str, app: FastAPI) -> None:
    spec = app.openapi()
    schemas = spec["components"]["schemas"]
    exempt = _exclude_flagged(app, spec) | _sse_payloads() | _FRAMEWORK_SCHEMAS | _request_reached_anywhere()
    partial_status = {
        cls.__name__
        for cls in _subclasses(CommonProblemMembers)
        if (field := cls.model_fields.get("status")) is not None and field.annotation == int | None
    }

    optional = {
        schema_name: sorted(missing)
        for schema_name, schema in schemas.items()
        if schema_name not in exempt and schema.get("type") == "object" and schema.get("properties")
        if (missing := _optional_properties(schema_name, schema, partial_status))
    }

    assert not optional, f"{name}: response-only schemas with optional properties; subclass WireModel: {optional}"


def test_optional_problem_status_exemption_does_not_hide_other_optional_fields() -> None:
    schema: dict[str, Any] = {"properties": {"status": {}, "detail": {}}, "required": []}
    assert _optional_properties("ExampleProblem", schema, {"ExampleProblem"}) == {"detail"}
    assert _optional_properties("Other", schema, {"ExampleProblem"}) == {"status", "detail"}


@pytest.mark.parametrize(("name", "app"), _apps(), ids=lambda value: value if isinstance(value, str) else "")
def test_a_route_that_drops_fields_keeps_its_schema_unsplit(name: str, app: FastAPI) -> None:
    spec = app.openapi()
    schemas = spec["components"]["schemas"]

    split = {
        flagged
        for flagged in _exclude_flagged(app, spec)
        if f"{flagged}-Input" in schemas or f"{flagged}-Output" in schemas
    }

    assert not split, f"{name}: a split mints a component the released surface never had: {split}"


@pytest.mark.parametrize(("name", "app"), _apps(), ids=lambda value: value if isinstance(value, str) else "")
def test_a_model_that_is_sometimes_sent_without_a_field_does_not_subclass_the_wire_base(
    name: str, app: FastAPI
) -> None:
    flagged = _exclude_flagged(app, app.openapi())
    sometimes_partial = [
        cls
        for cls in _subclasses(WireModel)
        if issubclass(cls, SseFramePayload) or (cls.__module__.startswith("blizzard") and cls.__name__ in flagged)
    ]

    assert not sometimes_partial, f"{name}: required fields the server can omit mislead the client: {sometimes_partial}"


def test_exclude_flag_detection_looks_through_list_and_optional_response_models() -> None:
    class InList(WireModel):
        value: int

    class InOptional(WireModel):
        value: int

    app = FastAPI()

    @app.get("/api/many", response_model=list[InList], response_model_exclude_none=True)
    def many() -> list[InList]:
        return []

    @app.get("/api/maybe", response_model=InOptional | None, response_model_exclude_unset=True)
    def maybe() -> InOptional | None:
        return None

    assert _exclude_flagged(app, app.openapi()) == {"InList", "InOptional"}
