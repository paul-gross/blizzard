"""The wire schemas' ``required`` lists (unit tier): in each exported spec, a response-only component
schema lists every property as required, because the server always sends it. The sets are derived from the
built apps and their specs, so a model added later without the wire base fails here (``bzh:wire-sent-is-required``)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from blizzard.cli.config_inputs import packaged_flag_registry
from blizzard.hub.app import create_app_for_export as build_hub_app
from blizzard.runner.app import create_app_for_export as build_runner_app
from blizzard.wire.sse import SseFramePayload
from blizzard.wire.sse_runner import RunnerScopedPayload

pytestmark = pytest.mark.unit

_REF_PREFIX = "#/components/schemas/"

#: FastAPI's own 422 body shapes, which no blizzard model owns.
_FRAMEWORK_SCHEMAS = frozenset({"HTTPValidationError", "ValidationError"})


def _subclasses(cls: type) -> Iterator[type]:
    for sub in cls.__subclasses__():
        yield sub
        yield from _subclasses(sub)


def _refs(node: object) -> Iterator[str]:
    """Every component schema name ``$ref``-ed anywhere under ``node``."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith(_REF_PREFIX):
                yield value.removeprefix(_REF_PREFIX)
            else:
                yield from _refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _refs(item)


def _closure(roots: set[str], schemas: dict[str, dict]) -> set[str]:
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


def _request_reached(spec: dict) -> set[str]:
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
            if isinstance(operation, dict)
            for name in _refs(operation.get("requestBody", {}))
        }
    )
    return _closure(bodies, schemas)


def _exclude_flagged(app: FastAPI, spec: dict) -> set[str]:
    """Schemas served by a route that drops fields from its reply (``response_model_exclude_*``)."""
    flagged: set[str] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not (
            route.response_model_exclude_none
            or route.response_model_exclude_unset
            or route.response_model_exclude_defaults
        ):
            continue
        for method in route.methods or ():
            operation = spec["paths"][route.path_format][method.lower()]
            flagged |= set(_refs(operation.get("responses", {})))
    return _closure(flagged, spec["components"]["schemas"])


def _sse_payloads() -> set[str]:
    return {cls.__name__ for cls in (*_subclasses(SseFramePayload), SseFramePayload, RunnerScopedPayload)}


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

    optional = {
        schema_name: sorted(set(schema["properties"]) - set(schema.get("required", [])))
        for schema_name, schema in schemas.items()
        if schema_name not in exempt and schema.get("type") == "object" and schema.get("properties")
        if set(schema["properties"]) - set(schema.get("required", []))
    }

    assert not optional, f"{name}: response-only schemas with optional properties; subclass WireModel: {optional}"


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
