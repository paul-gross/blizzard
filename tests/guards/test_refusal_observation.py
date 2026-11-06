"""The observation comparison rejects status, media and model drift."""

from copy import deepcopy
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel

from tests.support.refusal_observation import (
    RefusalObservation,
    coverage_mismatches,
    refusal_mismatches,
    unobserved_variants,
)


class Denied(BaseModel):
    reason: str


class Conflict(BaseModel):
    reason: str


def _example() -> tuple[dict[str, Any], RefusalObservation]:
    app = FastAPI()

    @app.get("/item", responses={409: {"model": Denied | Conflict}})
    def item() -> JSONResponse:
        return JSONResponse(status_code=409, content={"reason": "denied"})

    response = TestClient(app).get("/item")
    return app.openapi(), RefusalObservation(
        "GET", "/item", response.status_code, response.headers["content-type"], Denied, response.json()
    )


def test_runtime_refusal_matches_its_documented_variant() -> None:
    spec, observation = _example()
    assert refusal_mismatches(spec, observation) == []


def test_missing_variant_cannot_be_borrowed_from_another_status() -> None:
    spec, observation = _example()
    planted = deepcopy(spec)
    responses = planted["paths"]["/item"]["get"]["responses"]
    responses["410"] = deepcopy(responses["409"])
    responses["409"]["content"]["application/json"]["schema"] = {"$ref": "#/components/schemas/Conflict"}
    assert refusal_mismatches(planted, observation) == ["missing model: Denied"]


def test_wrong_model_is_not_accepted_even_with_same_body_shape() -> None:
    spec, observation = _example()
    planted = deepcopy(spec)
    responses = planted["paths"]["/item"]["get"]["responses"]
    responses["409"]["content"]["application/json"]["schema"] = {"$ref": "#/components/schemas/Conflict"}
    assert refusal_mismatches(planted, observation) == ["missing model: Denied"]


def test_media_mismatch_and_missing_status_are_rejected() -> None:
    spec, observation = _example()
    planted = deepcopy(spec)
    responses = planted["paths"]["/item"]["get"]["responses"]
    responses["409"]["content"]["application/problem+json"] = responses["409"]["content"].pop("application/json")
    assert refusal_mismatches(planted, observation) == ["missing media type: application/json"]
    del responses["409"]
    assert refusal_mismatches(planted, observation) == ["missing status: 409"]


def test_documented_model_does_not_validate_a_wrong_body() -> None:
    spec, observation = _example()
    invalid = RefusalObservation(
        observation.method, observation.path, observation.status, observation.media_type, Denied, {}
    )
    assert refusal_mismatches(spec, invalid)[0].startswith("invalid Denied body:")


def test_dangling_declaration_is_not_coverage() -> None:
    spec, observation = _example()
    del spec["components"]["schemas"]["Denied"]
    assert refusal_mismatches(spec, observation) == ["missing component: Denied"]


def test_each_declared_variant_requires_an_observation() -> None:
    spec, _ = _example()
    assert unobserved_variants(spec, "GET", "/item", 409, "application/json", {Denied}) == {"Conflict"}
    assert unobserved_variants(spec, "GET", "/item", 409, "application/json", {Denied, Conflict}) == set()


def test_coverage_refuses_unnamed_problem_schema() -> None:
    spec, _ = _example()
    content = spec["paths"]["/item"]["get"]["responses"]["409"]["content"]
    content.clear()
    content["application/problem+json"] = {"schema": {"type": "object"}}
    assert coverage_mismatches(spec, {("GET", "/item")}, []) == [
        "missing named variants: GET /item 409 application/problem+json"
    ]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ({}, "missing content: GET /item 409"),
        (
            {"application/json": {"schema": {"$ref": "#/components/schemas/Denied"}}},
            "unexpected media type: GET /item 409 application/json",
        ),
        ({"application/problem+json": {}}, "missing named variants: GET /item 409 application/problem+json"),
        (
            {"application/problem+json": {"schema": {"$ref": "#/components/schemas/Denied"}}},
            "non-problem model: GET /item 409 application/problem+json Denied",
        ),
        (
            {
                "application/problem+json": {
                    "schema": {"anyOf": [{"$ref": "#/components/schemas/Denied"}, {"type": "object"}]}
                }
            },
            "missing named variants: GET /item 409 application/problem+json",
        ),
    ],
)
def test_coverage_detects_bad_refusal_declarations(content: dict[str, Any], expected: str) -> None:
    spec, _ = _example()
    spec["paths"]["/item"]["get"]["responses"]["409"]["content"] = content
    assert expected in coverage_mismatches(spec, {("GET", "/item")}, [])


def test_only_fastapi_validation_response_is_exempt() -> None:
    spec, _ = _example()
    responses = spec["paths"]["/item"]["get"]["responses"]
    responses.clear()
    responses["422"] = {
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/HTTPValidationError"}}}
    }
    assert coverage_mismatches(spec, {("GET", "/item")}, []) == []
    responses["422"]["content"]["application/json"]["schema"] = {"$ref": "#/components/schemas/Denied"}
    assert coverage_mismatches(spec, {("GET", "/item")}, []) == [
        "unexpected media type: GET /item 422 application/json"
    ]
