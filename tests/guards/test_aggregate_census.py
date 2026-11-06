"""Planted cases for the discovery-to-aggregate census."""

from typing import Any

from pydantic import BaseModel

from tests.support.aggregate_census import aggregate_assertion_lines, credited, missing_operations
from tests.support.refusal_observation import RefusalObservation, coverage_mismatches


class Denied(BaseModel):
    reason: str


def test_only_asserted_aggregate_calls_count() -> None:
    source = """def test_example():
    refusal_mismatches(spec, observation)
    coverage_mismatches(spec, operations, observations)
    assert coverage_mismatches(spec, operations, observations) == []
"""
    assert aggregate_assertion_lines(source) == {4}
    assert not aggregate_assertion_lines(
        source.replace("assert coverage_mismatches", "result = coverage_mismatches").replace(" == []", "")
    )


def test_coverage_requires_observation_for_the_exact_operation_and_no_failures() -> None:
    observation = RefusalObservation("GET", "/one", 403, "application/problem+json", Denied, {"reason": "no"})
    operations = {("get", "/one"), ("get", "/two")}
    assert credited(operations, [observation], []) == {("GET", "/one")}
    assert credited(operations, [observation], ["unobserved variant"]) == set()
    assert credited(operations, [], []) == set()


def test_new_reach_and_missing_route_are_not_hidden_by_other_coverage() -> None:
    reaches = {("GET", "/items/{id}"), ("POST", "/new"), ("GET", "/missing")}
    routes = {("GET", "/items/{item_id}"), ("POST", "/new")}
    missing, unmatched = missing_operations(reaches, routes, {("GET", "/items/{item_id}")})
    assert missing == {("POST", "/new")}
    assert unmatched == {("GET", "/missing")}


def test_discovered_operation_is_checked_against_every_declared_variant() -> None:
    def ref(name: str) -> dict[str, str]:
        return {"$ref": f"#/components/schemas/{name}"}

    content = {"schema": {"oneOf": [ref("Denied"), ref("Other")]}}
    spec: dict[str, Any] = {
        "paths": {
            "/one": {
                "get": {
                    "responses": {
                        "403": {"content": {"application/problem+json": content}},
                        "409": {"content": {"application/problem+json": {"schema": ref("Denied")}}},
                    }
                }
            }
        },
        "components": {
            "schemas": {
                name: {"properties": {key: {} for key in ("type", "title", "detail", "instance")}}
                for name in ("Denied", "Other")
            }
        },
    }
    observation = RefusalObservation("GET", "/one", 403, "application/problem+json", Denied, {"reason": "no"})
    assert coverage_mismatches(spec, {("GET", "/one")}, [observation]) == [
        "unobserved: GET /one 403 application/problem+json Other",
        "unobserved: GET /one 409 application/problem+json Denied",
    ]
