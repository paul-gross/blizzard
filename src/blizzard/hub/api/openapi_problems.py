"""OpenAPI responses for every problem variant a hub operation declares."""

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from blizzard.foundation.web import SchemaComponentCollision
from blizzard.hub.api.problem_declarations import HUB_PROBLEM_DECLARATIONS
from blizzard.hub.api.problem_rendering import PROBLEM_MEDIA_TYPE


def install_problem_responses(app: FastAPI) -> None:
    """Add each declared problem variant without replacing existing response media."""
    original: Callable[[], dict[str, Any]] = app.openapi

    def openapi() -> dict[str, Any]:
        spec = original()
        grouped: dict[tuple[str, str, int], list[type[BaseModel]]] = defaultdict(list)
        for declaration in HUB_PROBLEM_DECLARATIONS:
            models = grouped[declaration.method, declaration.path, declaration.status_code]
            if declaration.model not in models:
                models.append(declaration.model)
        models = {model for variants in grouped.values() for model in variants}
        _, definitions = models_json_schema(
            [(model, "serialization") for model in models], ref_template="#/components/schemas/{model}"
        )
        schemas = spec.setdefault("components", {}).setdefault("schemas", {})
        for name, schema in definitions.get("$defs", {}).items():
            if name in schemas and schemas[name] != schema:
                raise SchemaComponentCollision(f"component {name!r} collides with a different route schema")
            schemas[name] = schema
        for (method, path, status), variants in grouped.items():
            responses = spec["paths"][path.replace("{name:path}", "{name}")][method.lower()]["responses"]
            response = responses.setdefault(str(status), {"description": "Problem"})
            refs = [{"$ref": f"#/components/schemas/{model.__name__}"} for model in variants]
            schema = refs[0] if len(refs) == 1 else {"anyOf": refs}
            response.setdefault("content", {})[PROBLEM_MEDIA_TYPE] = {"schema": schema}
        return spec

    app.openapi = openapi
