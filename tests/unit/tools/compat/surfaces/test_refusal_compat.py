"""Refusal response contracts participate in wire and HTTP API compatibility."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import BaseModel

from blizzard.tools.compat import allowances, gate
from blizzard.tools.compat.surfaces.api import classify_api
from blizzard.tools.compat.surfaces.wire import classify_spec_diff, is_wire_operation
from blizzard.wire.release import ReleaseProblem, ReleaseRefusal
from blizzard.wire.runner import (
    RunnerIdentityProblem,
    RunnerIdentityRefusal,
    RunnerVersionProblem,
    RunnerVersionRefusal,
)


def _spec(path: str = "/api/fleet/things") -> dict[str, Any]:
    return {
        "paths": {
            path: {
                "get": {
                    "responses": {
                        "200": {"content": {"application/json": {"schema": {"type": "string"}}}},
                        "409": {
                            "content": {
                                "application/problem+json": {
                                    "schema": {
                                        "oneOf": [
                                            {"$ref": "#/components/schemas/Denied"},
                                            {"$ref": "#/components/schemas/Busy"},
                                        ]
                                    }
                                }
                            }
                        },
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "Denied": {"type": "object", "properties": {"detail": {"type": "string"}}, "required": ["detail"]},
                "Busy": {"type": "object", "properties": {"reason": {"type": "string"}}},
            }
        },
    }


@pytest.mark.parametrize("change", ["status", "media", "schema", "variant", "property", "type"])
def test_refusal_losses_are_reported(change: str) -> None:
    base = _spec()
    head = copy.deepcopy(base)
    responses = head["paths"]["/api/fleet/things"]["get"]["responses"]
    content = responses["409"]["content"]
    schema = content["application/problem+json"]["schema"]
    denied = head["components"]["schemas"]["Denied"]
    if change == "status":
        del responses["409"]
    elif change == "media":
        content["application/json"] = content.pop("application/problem+json")
    elif change == "schema":
        del content["application/problem+json"]["schema"]
    elif change == "variant":
        schema["oneOf"].pop()
    elif change == "property":
        del denied["properties"]["detail"]
    else:
        denied["properties"]["detail"] = {"type": "integer"}
    assert classify_spec_diff(base, head)


def test_refusal_reaches_non_fleet_http_api_and_deduplicates_wire() -> None:
    for path, surface in [("/api/fleet/things", "wire"), ("/api/operator/things", "http-api")]:
        base = _spec(path)
        head = copy.deepcopy(base)
        del head["components"]["schemas"]["Denied"]["properties"]["detail"]
        wire = classify_spec_diff(base, head)
        findings = wire + classify_api(base, head, wire=wire)
        assert {(f.surface, f.where) for f in findings} == {
            (surface, "Denied.detail"),
            (surface, f"GET {path} 409 application/problem+json Denied.detail"),
        }


@pytest.mark.parametrize("change", ["remove", "retype"])
def test_refusal_component_field_findings_identify_every_position(change: str) -> None:
    base = _spec()
    base["paths"]["/api/operator/things"] = copy.deepcopy(base["paths"]["/api/fleet/things"])
    head = copy.deepcopy(base)
    properties = head["components"]["schemas"]["Denied"]["properties"]
    if change == "remove":
        del properties["detail"]
    else:
        properties["detail"]["type"] = "integer"
    wire = classify_spec_diff(base, head)
    findings = wire + classify_api(base, head, wire=wire)
    reason = "response property removed" if change == "remove" else "response property type changed"
    assert {(f.surface, f.where) for f in findings if f.reason == reason} >= {
        ("wire", "GET /api/fleet/things 409 application/problem+json Denied.detail"),
        ("http-api", "GET /api/operator/things 409 application/problem+json Denied.detail"),
    }


@pytest.mark.parametrize("change", ["remove", "retype"])
def test_component_allowance_admits_positional_copies_without_hiding_them(change: str) -> None:
    base = _spec()
    base["paths"]["/api/fleet/other"] = copy.deepcopy(base["paths"]["/api/fleet/things"])
    head = copy.deepcopy(base)
    properties = head["components"]["schemas"]["Denied"]["properties"]
    if change == "remove":
        del properties["detail"]
    else:
        properties["detail"]["type"] = "integer"
    reason = "response property removed" if change == "remove" else "response property type changed"
    findings = [v for v in classify_spec_diff(base, head) if v.reason == reason]
    entry = allowances.Allowance("0.2", "wire", "Denied.detail", reason, "reviewed")
    allowed = allowances.admitting([entry], (0, 3), ["v0.2.0"])
    verdicts = gate.judge({"v0.2.0": findings}, acknowledged=False, line=True, allowed=allowed)
    assert {v.violation.where for v in verdicts} == {
        "Denied.detail",
        "GET /api/fleet/things 409 application/problem+json Denied.detail",
        "GET /api/fleet/other 409 application/problem+json Denied.detail",
    }
    assert all(v.passes and v.allowance == entry for v in verdicts)
    positional = next(v.violation for v in verdicts if v.violation.component_where is not None)
    specific = allowances.Allowance("0.2", "wire", positional.where, reason, "position reviewed")
    [specific_verdict] = gate.judge(
        {"v0.2.0": [positional]},
        acknowledged=False,
        line=True,
        allowed=allowances.admitting([entry, specific], (0, 3), ["v0.2.0"]),
    )
    assert specific_verdict.allowance == specific
    for wrong in (
        allowances.Allowance("0.2", "http-api", "Denied.detail", reason, "reviewed"),
        allowances.Allowance("0.2", "wire", "Denied.other", reason, "reviewed"),
        allowances.Allowance("0.2", "wire", "Denied.detail", "different reason", "reviewed"),
        allowances.Allowance("0.1", "wire", "Denied.detail", reason, "reviewed"),
    ):
        unmatched = gate.judge(
            {"v0.2.0": findings},
            acknowledged=False,
            line=True,
            allowed=allowances.admitting([wrong], (0, 3), ["v0.2.0"]),
        )
        assert all(not v.passes and v.allowance is None for v in unmatched)
    assert all(
        not v.passes
        for v in gate.judge(
            {"v0.2.0": findings},
            acknowledged=False,
            line=True,
            allowed=allowances.admitting([entry], (0, 2), ["v0.2.0"]),
        )
    )


def test_inline_refusal_schema_is_compared() -> None:
    base = _spec()
    head = copy.deepcopy(base)
    old = {"type": "object", "properties": {"detail": {"type": "string"}}, "required": ["detail"]}
    new = {"type": "object", "properties": {"detail": {"type": "integer"}}}
    for doc, schema in ((base, old), (head, new)):
        doc["paths"]["/api/fleet/things"]["get"]["responses"]["409"]["content"]["application/problem+json"][
            "schema"
        ] = schema
    reasons = {v.reason for v in classify_spec_diff(base, head)}
    assert "response property type changed" in reasons
    assert "response property stopped being required" in reasons


def test_adding_refusal_and_keeping_old_media_is_additive() -> None:
    base = _spec()
    head = copy.deepcopy(base)
    responses = head["paths"]["/api/fleet/things"]["get"]["responses"]
    responses["404"] = {"content": {"application/problem+json": {"schema": {"type": "string"}}}}
    responses["409"]["content"]["application/json"] = {"schema": {"type": "string"}}
    assert classify_spec_diff(base, head) == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/me"),
        ("get", "/api/fleet/chunks/{chunk_id}/transcript-segments"),
        ("post", "/api/hosts"),
        ("post", "/api/fleet/host"),
        ("get", "/api/auth/authorize"),
        ("get", "/api/auth/jwks.json"),
    ],
)
def test_discovered_runner_calls_are_wire_operations(method: str, path: str) -> None:
    assert is_wire_operation(path, method)


@pytest.mark.parametrize(
    ("method", "path", "code", "old_name", "new_name", "kind"),
    [
        (
            "post",
            "/api/fleet/chunks/{chunk_id}/release",
            "409",
            "ReleaseRefusal",
            "ReleaseProblem",
            "urn:blizzard:problem:release-refusal",
        ),
        (
            "get",
            "/api/fleet/identity",
            "401",
            "RunnerIdentityRefusal",
            "RunnerIdentityProblem",
            "urn:blizzard:problem:runner-identity",
        ),
        (
            "post",
            "/api/fleet/runners",
            "409",
            "RunnerVersionRefusal",
            "RunnerVersionProblem",
            "urn:blizzard:problem:runner-version",
        ),
    ],
)
def test_known_migration_requires_identity_and_preserved_shape(
    method: str, path: str, code: str, old_name: str, new_name: str, kind: str
) -> None:
    old = {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}
    new = {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "deprecated": True},
            "type": {"type": "string", "const": kind},
            "title": {"type": "string"},
            "detail": {"type": "string"},
            "instance": {"type": "string"},
        },
        "required": ["reason", "type", "title", "detail", "instance"],
    }

    def ref(name: str) -> dict[str, str]:
        return {"$ref": f"#/components/schemas/{name}"}

    base: dict[str, Any] = {
        "paths": {path: {method: {"responses": {code: {"content": {"application/json": {"schema": ref(old_name)}}}}}}},
        "components": {"schemas": {old_name: old}},
    }
    head: dict[str, Any] = {
        "paths": {
            path: {method: {"responses": {code: {"content": {"application/problem+json": {"schema": ref(new_name)}}}}}}
        },
        "components": {"schemas": {new_name: new}},
    }
    assert classify_spec_diff(base, head) == []
    changed = copy.deepcopy(head)
    changed["components"]["schemas"][new_name]["properties"]["reason"]["type"] = "integer"
    assert classify_spec_diff(base, changed)
    changed = copy.deepcopy(head)
    changed["components"]["schemas"][new_name]["required"].remove("reason")
    assert classify_spec_diff(base, changed)
    changed = copy.deepcopy(head)
    changed["paths"][path][method]["responses"][code]["content"]["application/json"] = {"schema": ref(old_name)}
    assert any(v.where == old_name for v in classify_spec_diff(base, changed))
    changed = copy.deepcopy(head)
    changed["components"]["schemas"][new_name]["properties"]["type"]["const"] = "unrelated"
    assert classify_spec_diff(base, changed)
    changed = copy.deepcopy(base)
    changed["paths"][path][method]["responses"][code]["content"]["application/json"]["schema"] = ref("Other")
    assert classify_spec_diff(changed, head)


def test_inline_union_changes_are_not_hidden_by_identical_object_signatures() -> None:
    base = _spec()
    head = copy.deepcopy(base)

    def union(doc: dict[str, Any]) -> list[dict[str, Any]]:
        schema = doc["paths"]["/api/fleet/things"]["get"]["responses"]["409"]["content"]["application/problem+json"][
            "schema"
        ]
        schema.clear()
        schema["oneOf"] = [
            {"type": "object", "properties": {"kind": {"type": "string", "enum": ["a"]}}},
            {"type": "object", "properties": {"kind": {"type": "string", "enum": ["b"]}}},
        ]
        return schema["oneOf"]

    union(base)
    members = union(head)
    members.reverse()
    assert classify_spec_diff(base, head) == []
    members[0]["properties"]["kind"]["enum"] = ["c"]
    assert any(v.reason == "inline response union changed" for v in classify_spec_diff(base, head))
    members.pop()
    assert any(v.reason == "inline response union changed" for v in classify_spec_diff(base, head))


def test_inline_union_in_referenced_component_is_compared() -> None:
    base = _spec()
    head = copy.deepcopy(base)
    for doc, value in ((base, "a"), (head, "c")):
        doc["paths"]["/api/fleet/things"]["get"]["responses"]["409"]["content"]["application/problem+json"][
            "schema"
        ] = {"$ref": "#/components/schemas/Union"}
        doc["components"]["schemas"]["Union"] = {
            "oneOf": [
                {"type": "object", "properties": {"kind": {"type": "string", "enum": [value]}}},
                {"type": "object", "properties": {"kind": {"type": "string", "enum": ["b"]}}},
            ]
        }
    assert any(
        v.where == "Union" and v.reason == "inline response union changed" for v in classify_spec_diff(base, head)
    )


@pytest.mark.parametrize(
    ("method", "path", "code", "legacy", "problem"),
    [
        ("post", "/api/fleet/chunks/{chunk_id}/release", "409", ReleaseRefusal, ReleaseProblem),
        ("get", "/api/fleet/identity", "401", RunnerIdentityRefusal, RunnerIdentityProblem),
        ("post", "/api/fleet/runners", "409", RunnerVersionRefusal, RunnerVersionProblem),
    ],
)
def test_known_migrations_match_wire_model_schemas(
    method: str, path: str, code: str, legacy: type[BaseModel], problem: type[BaseModel]
) -> None:
    def spec(model: type[BaseModel], media: str) -> dict[str, Any]:
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        components = schema.pop("$defs", {})
        components[model.__name__] = schema
        return {
            "paths": {
                path: {
                    method: {
                        "responses": {
                            code: {"content": {media: {"schema": {"$ref": f"#/components/schemas/{model.__name__}"}}}}
                        }
                    }
                }
            },
            "components": {"schemas": components},
        }

    assert classify_spec_diff(spec(legacy, "application/json"), spec(problem, "application/problem+json")) == []
