"""Compare a targeted observed refusal with its operation's OpenAPI response."""

from dataclasses import dataclass
from typing import Any

from httpx2 import Response
from pydantic import BaseModel, ValidationError


@dataclass(frozen=True)
class RefusalObservation:
    method: str
    path: str
    status: int
    media_type: str
    model: type[BaseModel]
    body: object


def observe_refusal(
    observations: list[RefusalObservation],
    response: Response,
    method: str,
    path: str,
    status: int,
    model: type[BaseModel],
    *,
    problem: bool = True,
) -> RefusalObservation:
    """Record ``response`` as a refusal of ``method path``; ``problem`` also asserts a problem document for this request."""
    assert response.status_code == status, response.text
    if problem:
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["instance"] == response.request.url.path
    observation = RefusalObservation(method, path, status, response.headers["content-type"], model, response.json())
    observations.append(observation)
    return observation


def _model_refs(schema: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        refs.add(ref.removeprefix("#/components/schemas/"))
    for keyword in ("oneOf", "anyOf", "allOf"):
        for variant in schema.get(keyword, []):
            refs.update(_model_refs(variant))
    return refs


def _named_schema(schema: dict[str, Any]) -> bool:
    if isinstance(schema.get("$ref"), str):
        return schema["$ref"].startswith("#/components/schemas/")
    variants = [variant for key in ("oneOf", "anyOf", "allOf") for variant in schema.get(key, [])]
    return bool(variants) and all(_named_schema(variant) for variant in variants)


def refusal_mismatches(spec: dict[str, Any], observation: RefusalObservation) -> list[str]:
    """Check only the observed operation, status, media type and named model."""
    operation = spec.get("paths", {}).get(observation.path, {}).get(observation.method.lower())
    if operation is None:
        return [f"missing operation: {observation.method} {observation.path}"]
    response = operation.get("responses", {}).get(str(observation.status))
    if response is None:
        return [f"missing status: {observation.status}"]
    media_type = observation.media_type.split(";", 1)[0].strip().lower()
    content = response.get("content", {}).get(media_type)
    if content is None:
        return [f"missing media type: {media_type}"]
    model_name = observation.model.__name__
    if model_name not in _model_refs(content.get("schema", {})):
        return [f"missing model: {model_name}"]
    if model_name not in spec.get("components", {}).get("schemas", {}):
        return [f"missing component: {model_name}"]
    try:
        observation.model.model_validate(observation.body)
    except ValidationError as exc:
        return [f"invalid {model_name} body: {exc}"]
    return []


def unobserved_variants(
    spec: dict[str, Any], method: str, path: str, status: int, media_type: str, models: set[type[BaseModel]]
) -> set[str]:
    """Find declared variants not exercised for one operation and response."""
    schema = spec["paths"][path][method.lower()]["responses"][str(status)]["content"][media_type]["schema"]
    return _model_refs(schema) - {model.__name__ for model in models}


def coverage_mismatches(
    spec: dict[str, Any], operations: set[tuple[str, str]], observations: list[RefusalObservation]
) -> list[str]:
    """Compare every problem response of the selected operations with actual observations.

    An observation only covers its exact operation, status, media and model, and
    only after its response body and declaration have both been validated.
    """
    covered: set[tuple[str, str, str, str, str]] = set()
    errors: list[str] = []
    for observation in observations:
        method = observation.method.lower()
        failures = refusal_mismatches(spec, observation)
        errors.extend(f"{method.upper()} {observation.path}: {failure}" for failure in failures)
        if not failures:
            media = observation.media_type.split(";", 1)[0].strip().lower()
            covered.add((method, observation.path, str(observation.status), media, observation.model.__name__))

    for method, path in sorted(operations):
        method = method.lower()
        operation = spec.get("paths", {}).get(path, {}).get(method)
        if operation is None:
            errors.append(f"missing operation: {method.upper()} {path}")
            continue
        for status, response in operation.get("responses", {}).items():
            if len(status) != 3 or status[0] not in "45" or not status.isdigit():
                continue
            contents = response.get("content", {})
            validation = {"$ref": "#/components/schemas/HTTPValidationError"}
            for media, content in contents.items():
                if status == "422" and media == "application/json" and content.get("schema") == validation:
                    continue
                if media != "application/problem+json":
                    errors.append(f"unexpected media type: {method.upper()} {path} {status} {media}")
                    continue
                models = _model_refs(content.get("schema", {}))
                if not _named_schema(content.get("schema", {})):
                    errors.append(f"missing named variants: {method.upper()} {path} {status} {media}")
                for model in sorted(models):
                    component = spec.get("components", {}).get("schemas", {}).get(model, {})
                    if not {"type", "title", "detail", "instance"} <= component.get("properties", {}).keys():
                        errors.append(f"non-problem model: {method.upper()} {path} {status} {media} {model}")
                    if (method, path, status, media, model) not in covered:
                        errors.append(f"unobserved: {method.upper()} {path} {status} {media} {model}")
            if not contents:
                errors.append(f"missing content: {method.upper()} {path} {status}")
    return errors
