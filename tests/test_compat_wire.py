"""blizzard:compat (bzh:released-surface-additive) — the wire classifier over hand-built fixture
specs, never the real committed one."""

from __future__ import annotations

import copy
from enum import StrEnum
from typing import Annotated

import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from blizzard.foundation.unrecognized import Unrecognized
from blizzard.tools.compat.surfaces.api import classify_api
from blizzard.tools.compat.surfaces.wire import classify_spec_diff
from blizzard.wire.vocabulary import OPEN

pytestmark = pytest.mark.unit


# --- fixture-spec helpers -----------------------------------------------------------


def _spec(paths: dict, schemas: dict | None = None) -> dict:
    return {"openapi": "3.1.0", "paths": paths, "components": {"schemas": schemas or {}}}


def _ref(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}"}


def _op(*, response_schema: dict | None = None, request_schema: dict | None = None) -> dict:
    op: dict = {"responses": {}}
    if response_schema is not None:
        op["responses"]["200"] = {"content": {"application/json": {"schema": response_schema}}}
    else:
        op["responses"]["204"] = {"description": "no content"}
    if request_schema is not None:
        op["requestBody"] = {"content": {"application/json": {"schema": request_schema}}}
    return op


def _violation_texts(violations) -> list[str]:
    return [str(v) for v in violations]


# --- surface honored ------------------------------------------------------------------


def test_new_route_is_additive() -> None:
    base = _spec({})
    head = _spec({"/api/fleet/widgets": {"get": _op()}})
    assert classify_spec_diff(base, head) == []


def test_surface_path_removed_is_breaking() -> None:
    base = _spec({"/api/fleet/widgets": {"get": _op()}})
    head = _spec({})
    assert any("surface path removed" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_surface_method_removed_is_breaking() -> None:
    base = _spec({"/api/fleet/widgets": {"get": _op(), "post": _op()}})
    head = _spec({"/api/fleet/widgets": {"get": _op()}})
    assert any("surface method removed" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_kept_counterpart_beside_its_replacement_is_additive() -> None:
    base = _spec({"/api/fleet/queue/peek": {"get": _op()}})
    head = _spec({"/api/fleet/queue/peek": {"get": _op(), "post": _op()}})
    assert classify_spec_diff(base, head) == []


def test_non_surface_path_is_ignored() -> None:
    schemas_base = {"Thing": {"type": "object", "properties": {"a": {"type": "string"}}}}
    schemas_head = {"Thing": {"type": "object", "properties": {}}}
    base = _spec({"/api/chunks/{id}": {"get": _op(response_schema=_ref("Thing"))}}, schemas_base)
    head = _spec({}, schemas_head)
    assert classify_spec_diff(base, head) == []


def test_auth_federation_routes_are_on_the_surface() -> None:
    base = _spec({"/api/auth/jwks.json": {"get": _op()}})
    head = _spec({})
    assert any("surface path removed" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_the_runner_add_call_is_on_the_surface() -> None:
    base = _spec({"/api/runners": {"get": _op(), "post": _op()}})
    head = _spec({"/api/runners": {"get": _op()}})
    assert _violation_texts(classify_spec_diff(base, head)) == ["POST /api/runners: surface method removed"]


def test_the_runner_add_call_s_response_is_held_to_the_surface_rules() -> None:
    added = {"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]}
    base = _spec({"/api/runners": {"post": _op(response_schema=_ref("Added"))}}, {"Added": added})
    head = _spec({"/api/runners": {"post": _op(response_schema=_ref("Added"))}}, {"Added": {"type": "object"}})
    assert any("Added.token" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_the_operator_runner_list_beside_the_add_call_stays_off_the_surface() -> None:
    listed = {"type": "object", "properties": {"runners": {"type": "array"}}, "required": ["runners"]}
    base = _spec({"/api/runners": {"get": _op(response_schema=_ref("Listed")), "post": _op()}}, {"Listed": listed})
    head = _spec({"/api/runners": {"post": _op()}}, {"Listed": {"type": "object"}})
    assert classify_spec_diff(base, head) == []


def test_the_host_add_call_is_on_the_surface() -> None:
    added = {"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]}
    base = _spec({"/api/hosts": {"get": _op(), "post": _op(response_schema=_ref("Added"))}}, {"Added": added})
    head = _spec(
        {"/api/hosts": {"get": _op(), "post": _op(response_schema=_ref("Added"))}}, {"Added": {"type": "object"}}
    )
    assert any("Added.token" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_the_host_plane_is_on_the_surface() -> None:
    base = _spec({"/api/fleet/host": {"get": _op(), "post": _op()}})
    head = _spec({"/api/fleet/host": {"get": _op()}})
    assert _violation_texts(classify_spec_diff(base, head)) == ["POST /api/fleet/host: surface method removed"]


def test_the_operator_host_list_beside_the_add_call_stays_off_the_surface() -> None:
    base = _spec({"/api/hosts": {"get": _op(), "post": _op()}})
    head = _spec({"/api/hosts": {"post": _op()}})
    assert classify_spec_diff(base, head) == []


# --- response-reached schema classes --------------------------------------------------


def _response_widget_specs(base_schema: dict, head_schema: dict) -> tuple[dict, dict]:
    base = _spec({"/api/fleet/widgets": {"get": _op(response_schema=_ref("Widget"))}}, {"Widget": base_schema})
    head = _spec({"/api/fleet/widgets": {"get": _op(response_schema=_ref("Widget"))}}, {"Widget": head_schema})
    return base, head


def test_response_property_removed_is_breaking() -> None:
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}}},
        {"type": "object", "properties": {"id": {"type": "string"}}},
    )
    assert any("response property removed" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_response_optional_property_added_is_additive() -> None:
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"id": {"type": "string"}}},
        {"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}}},
    )
    assert classify_spec_diff(base, head) == []


def test_response_property_type_changed_is_breaking() -> None:
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"count": {"type": "string"}}},
        {"type": "object", "properties": {"count": {"type": "integer"}}},
    )
    assert any("response property type changed" in v for v in _violation_texts(classify_spec_diff(base, head)))


def _enum_narrowing_specs(*, request: bool, nullable: bool, base_enum: list[str] | None = None) -> tuple[dict, dict]:
    base_member: dict = {"type": "string"} if base_enum is None else {"type": "string", "enum": base_enum}
    base_prop: dict = {"anyOf": [base_member, {"type": "null"}]} if nullable else base_member
    head_prop: dict = {"anyOf": [_ref("Color"), {"type": "null"}]} if nullable else _ref("Color")
    op = (lambda s: _op(request_schema=s)) if request else (lambda s: _op(response_schema=s))
    color = {"type": "string", "enum": ["red", "blue"], "title": "Color"}
    base = _spec(
        {"/api/fleet/widgets": {"post": op(_ref("Widget"))}},
        {"Widget": {"type": "object", "properties": {"color": base_prop}}},
    )
    head = _spec(
        {"/api/fleet/widgets": {"post": op(_ref("Widget"))}},
        {"Widget": {"type": "object", "properties": {"color": head_prop}}, "Color": color},
    )
    return base, head


@pytest.mark.parametrize("nullable", [False, True])
def test_response_string_narrowed_to_enum_component_is_additive(nullable: bool) -> None:
    base, head = _enum_narrowing_specs(request=False, nullable=nullable)
    assert classify_spec_diff(base, head) == []


@pytest.mark.parametrize("nullable", [False, True])
def test_response_literal_moved_onto_same_enum_component_is_additive(nullable: bool) -> None:
    base, head = _enum_narrowing_specs(request=False, nullable=nullable, base_enum=["red", "blue"])
    assert classify_spec_diff(base, head) == []


@pytest.mark.parametrize("nullable", [False, True])
def test_response_literal_moved_onto_enum_component_gaining_a_value_is_breaking(nullable: bool) -> None:
    # An older runner's closed-`Literal` parse raises on `blue`, whichever schema spells it.
    base, head = _enum_narrowing_specs(request=False, nullable=nullable, base_enum=["red"])
    assert any("response enum gained value(s) ['blue']" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_request_string_narrowed_to_enum_component_is_breaking() -> None:
    base, head = _enum_narrowing_specs(request=True, nullable=False)
    assert any("request property type narrowed" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_response_string_retyped_to_object_component_is_breaking() -> None:
    base, head = _enum_narrowing_specs(request=False, nullable=False)
    head["components"]["schemas"]["Color"] = {"type": "object", "properties": {}}
    assert any("response property type changed" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_response_property_stops_being_required_is_breaking() -> None:
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
        {"type": "object", "properties": {"id": {"type": "string"}}, "required": []},
    )
    assert any(
        "response property stopped being required" in v for v in _violation_texts(classify_spec_diff(base, head))
    )


def test_response_property_stops_being_nullable_is_additive() -> None:
    # Safe direction: a runner tolerant of `None` still parses a value that is never `None`.
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"note": {"type": ["string", "null"]}}},
        {"type": "object", "properties": {"note": {"type": "string"}}},
    )
    assert classify_spec_diff(base, head) == []


def test_response_property_newly_becomes_nullable_is_breaking() -> None:
    # Breaking direction: an old runner's non-Optional parse model rejects the new `null`.
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"note": {"type": "string"}}},
        {"type": "object", "properties": {"note": {"type": ["string", "null"]}}},
    )
    assert any("response property newly became nullable" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_response_property_type_changed_under_anyof_optional_is_breaking() -> None:
    # pydantic's `X | None` compiles to `anyOf: [{...X}, {type: "null"}]`, with no top-level
    # `type` key — the signature must still resolve through the non-null member.
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"count": {"anyOf": [{"type": "string"}, {"type": "null"}]}}},
        {"type": "object", "properties": {"count": {"anyOf": [{"type": "integer"}, {"type": "null"}]}}},
    )
    assert any("response property type changed" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_response_property_same_anyof_optional_type_is_additive() -> None:
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"count": {"anyOf": [{"type": "string"}, {"type": "null"}]}}},
        {"type": "object", "properties": {"count": {"anyOf": [{"type": "string"}, {"type": "null"}]}}},
    )
    assert classify_spec_diff(base, head) == []


def test_response_enum_gains_value_is_breaking() -> None:
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"status": {"type": "string", "enum": ["ready", "done"]}}},
        {"type": "object", "properties": {"status": {"type": "string", "enum": ["ready", "done", "paused"]}}},
    )
    assert any("response enum gained value" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_response_enum_loses_value_is_additive() -> None:
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"status": {"type": "string", "enum": ["ready", "done", "paused"]}}},
        {"type": "object", "properties": {"status": {"type": "string", "enum": ["ready", "done"]}}},
    )
    assert classify_spec_diff(base, head) == []


def test_response_union_gains_member_is_breaking() -> None:
    extra_schemas = {
        "A": {"type": "object", "properties": {}},
        "B": {"type": "object", "properties": {}},
    }
    base_schema = {"type": "object", "properties": {"payload": {"anyOf": [_ref("A")]}}}
    head_schema = {"type": "object", "properties": {"payload": {"anyOf": [_ref("A"), _ref("B")]}}}
    base = _spec(
        {"/api/fleet/widgets": {"get": _op(response_schema=_ref("Widget"))}}, {"Widget": base_schema, **extra_schemas}
    )
    head = _spec(
        {"/api/fleet/widgets": {"get": _op(response_schema=_ref("Widget"))}}, {"Widget": head_schema, **extra_schemas}
    )
    assert any("response union gained member" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_property_added_to_forbid_schema_reached_by_response_is_breaking() -> None:
    base, head = _response_widget_specs(
        {"type": "object", "additionalProperties": False, "properties": {"id": {"type": "string"}}},
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"id": {"type": "string"}, "extra": {"type": "string"}},
        },
    )
    assert any(
        "property added to a forbid schema a response reaches" in v
        for v in _violation_texts(classify_spec_diff(base, head))
    )


# --- request-reached schema classes -----------------------------------------------------


def _request_widget_specs(base_schema: dict, head_schema: dict) -> tuple[dict, dict]:
    base = _spec({"/api/fleet/widgets": {"post": _op(request_schema=_ref("Widget"))}}, {"Widget": base_schema})
    head = _spec({"/api/fleet/widgets": {"post": _op(request_schema=_ref("Widget"))}}, {"Widget": head_schema})
    return base, head


def test_request_new_required_property_is_breaking() -> None:
    base, head = _request_widget_specs(
        {"type": "object", "properties": {"id": {"type": "string"}}, "required": []},
        {
            "type": "object",
            "properties": {"id": {"type": "string"}, "kind": {"type": "string"}},
            "required": ["kind"],
        },
    )
    assert any("request property became required" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_request_optional_property_becomes_required_is_breaking() -> None:
    base, head = _request_widget_specs(
        {"type": "object", "properties": {"id": {"type": "string"}}, "required": []},
        {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    )
    assert any("request property became required" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_request_optional_property_added_is_additive() -> None:
    base, head = _request_widget_specs(
        {"type": "object", "properties": {"id": {"type": "string"}}, "required": []},
        {"type": "object", "properties": {"id": {"type": "string"}, "note": {"type": "string"}}, "required": []},
    )
    assert classify_spec_diff(base, head) == []


def test_request_property_type_narrowed_is_breaking() -> None:
    base, head = _request_widget_specs(
        {"type": "object", "properties": {"count": {"type": ["string", "integer"]}}},
        {"type": "object", "properties": {"count": {"type": "string"}}},
    )
    assert any("request property type narrowed" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_request_property_drops_nullability_is_breaking() -> None:
    base, head = _request_widget_specs(
        {"type": "object", "properties": {"note": {"type": ["string", "null"]}}},
        {"type": "object", "properties": {"note": {"type": "string"}}},
    )
    assert any("request property dropped nullability" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_request_enum_loses_value_is_breaking() -> None:
    base, head = _request_widget_specs(
        {"type": "object", "properties": {"kind": {"type": "string", "enum": ["create", "update"]}}},
        {"type": "object", "properties": {"kind": {"type": "string", "enum": ["create"]}}},
    )
    assert any("request enum lost value" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_request_enum_gains_value_is_additive() -> None:
    base, head = _request_widget_specs(
        {"type": "object", "properties": {"kind": {"type": "string", "enum": ["create"]}}},
        {"type": "object", "properties": {"kind": {"type": "string", "enum": ["create", "update"]}}},
    )
    assert classify_spec_diff(base, head) == []


def test_property_removed_from_forbid_request_schema_is_breaking() -> None:
    base, head = _request_widget_specs(
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"id": {"type": "string"}, "note": {"type": "string"}},
        },
        {"type": "object", "additionalProperties": False, "properties": {"id": {"type": "string"}}},
    )
    assert any(
        "property removed from a forbid request schema" in v for v in _violation_texts(classify_spec_diff(base, head))
    )


def test_property_removed_from_non_forbid_request_schema_is_additive() -> None:
    base, head = _request_widget_specs(
        {"type": "object", "properties": {"id": {"type": "string"}, "note": {"type": "string"}}},
        {"type": "object", "properties": {"id": {"type": "string"}}},
    )
    assert classify_spec_diff(base, head) == []


def test_property_added_to_request_only_forbid_schema_is_additive() -> None:
    base, head = _request_widget_specs(
        {"type": "object", "additionalProperties": False, "properties": {"id": {"type": "string"}}},
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"id": {"type": "string"}, "extra": {"type": "string"}},
        },
    )
    assert classify_spec_diff(base, head) == []


def test_nested_schema_reachable_by_both_roles_keeps_both_roles() -> None:
    # "Priority" nests inside "Proposal", reached as a response by one op and a request by
    # another — the walk must give "Priority" both roles, not just whichever reaches it first.
    priority_base = {"type": "string", "enum": ["low", "high"]}
    priority_head = {"type": "string", "enum": ["low"]}
    proposal = {"type": "object", "properties": {"priority": _ref("Priority")}}
    paths = {
        "/api/fleet/proposals": {"get": _op(response_schema=_ref("Proposal"))},
        "/api/fleet/proposals/draft": {"post": _op(request_schema=_ref("Proposal"))},
    }
    base = _spec(paths, {"Proposal": proposal, "Priority": priority_base})
    head = _spec(paths, {"Proposal": proposal, "Priority": priority_head})
    assert any("request enum lost value" in v for v in _violation_texts(classify_spec_diff(base, head)))


def test_component_schema_deleted_while_still_referenced_is_breaking() -> None:
    base = _spec(
        {"/api/fleet/widgets": {"get": _op(response_schema=_ref("Widget"))}},
        {"Widget": {"type": "object", "properties": {}}},
    )
    head = _spec({"/api/fleet/widgets": {"get": _op(response_schema=_ref("Widget"))}}, {})
    assert any("component schema deleted" in v for v in _violation_texts(classify_spec_diff(base, head)))


# --- parameters, array items, map values ------------------------------------------------


def _param(name: str, schema: dict, *, where: str = "query", required: bool = False) -> dict:
    return {"name": name, "in": where, "required": required, "schema": schema}


def _params_specs(base_params: list[dict], head_params: list[dict], schemas: dict | None = None) -> tuple[dict, dict]:
    def build(params: list[dict]) -> dict:
        return _spec({"/api/fleet/widgets": {"get": {**_op(), "parameters": params}}}, schemas)

    return build(base_params), build(head_params)


def test_new_required_parameter_is_breaking() -> None:
    base, head = _params_specs([], [_param("kind", {"type": "string"}, required=True)])
    texts = _violation_texts(classify_spec_diff(base, head))
    assert any("GET /api/fleet/widgets parameter.query kind" in t and "became required" in t for t in texts)


def test_optional_parameter_becoming_required_is_breaking() -> None:
    base, head = _params_specs(
        [_param("kind", {"type": "string"})], [_param("kind", {"type": "string"}, required=True)]
    )
    assert classify_spec_diff(base, head)


def test_parameter_type_narrowed_is_breaking() -> None:
    base, head = _params_specs([_param("limit", {"type": "string"})], [_param("limit", {"type": "integer"})])
    assert any("type narrowed" in t for t in _violation_texts(classify_spec_diff(base, head)))


def test_parameter_inline_enum_losing_a_value_is_breaking() -> None:
    base, head = _params_specs(
        [_param("sort", {"type": "string", "enum": ["a", "b"]})], [_param("sort", {"type": "string", "enum": ["a"]})]
    )
    assert any("enum lost" in t for t in _violation_texts(classify_spec_diff(base, head)))


def test_new_optional_parameter_is_additive() -> None:
    base, head = _params_specs([], [_param("kind", {"type": "string"})])
    assert classify_spec_diff(base, head) == []


def test_removed_parameter_is_additive() -> None:
    base, head = _params_specs([_param("kind", {"type": "string"})], [])
    assert classify_spec_diff(base, head) == []


def test_parameter_referencing_an_enum_component_holds_the_request_rules() -> None:
    def schemas(values: list[str]) -> dict:
        return {"State": {"type": "string", "enum": values}}

    param = [_param("state", _ref("State"))]
    base, _ = _params_specs(param, param, schemas(["a", "b"]))
    _, head = _params_specs(param, param, schemas(["a"]))
    assert any("State" in t and "enum lost" in t for t in _violation_texts(classify_spec_diff(base, head)))
    _, gained = _params_specs(param, param, schemas(["a", "b", "c"]))
    assert classify_spec_diff(base, gained) == []


def _widget_property_specs(base_prop: dict, head_prop: dict, *, request: bool) -> tuple[dict, dict]:
    def build(prop: dict) -> dict:
        schemas = {"Widget": {"type": "object", "properties": {"p": prop}}}
        op = _op(request_schema=_ref("Widget")) if request else _op(response_schema=_ref("Widget"))
        return _spec({"/api/fleet/widgets": {"post": op}}, schemas)

    return build(base_prop), build(head_prop)


@pytest.mark.parametrize("wrap", [False, True])
def test_array_item_type_change_is_breaking(wrap: bool) -> None:
    def arr(item: str) -> dict:
        body = {"type": "array", "items": {"type": item}}
        return {"anyOf": [body, {"type": "null"}]} if wrap else body

    for request in (False, True):
        base, head = _widget_property_specs(arr("string"), arr("integer"), request=request)
        assert any("Widget.p[]" in t for t in _violation_texts(classify_spec_diff(base, head)))
    base, head = _widget_property_specs(arr("string"), arr("string"), request=False)
    assert classify_spec_diff(base, head) == []


@pytest.mark.parametrize("wrap", [False, True])
def test_map_value_type_change_is_breaking(wrap: bool) -> None:
    def mapping(value: str) -> dict:
        body = {"type": "object", "additionalProperties": {"type": value}}
        return {"anyOf": [body, {"type": "null"}]} if wrap else body

    base, head = _widget_property_specs(mapping("string"), mapping("integer"), request=False)
    assert any("Widget.p{}" in t for t in _violation_texts(classify_spec_diff(base, head)))


def test_response_array_item_newly_nullable_is_breaking() -> None:
    base, head = _widget_property_specs(
        {"type": "array", "items": {"type": "string"}},
        {"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
        request=False,
    )
    assert any("Widget.p[]" in t and "nullable" in t for t in _violation_texts(classify_spec_diff(base, head)))


def test_request_array_item_enum_losing_a_value_is_breaking() -> None:
    base, head = _widget_property_specs(
        {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}},
        {"type": "array", "items": {"type": "string", "enum": ["a"]}},
        request=True,
    )
    assert any("Widget.p[]" in t and "enum lost" in t for t in _violation_texts(classify_spec_diff(base, head)))


def test_component_reached_only_through_a_map_value_is_checked() -> None:
    def build(props: dict) -> dict:
        schemas = {
            "Holder": {"type": "object", "additionalProperties": _ref("Leaf")},
            "Leaf": {"type": "object", "properties": props},
        }
        return _spec({"/api/fleet/widgets": {"get": _op(response_schema=_ref("Holder"))}}, schemas)

    violations = classify_spec_diff(build({"a": {"type": "string"}}), build({}))
    assert any("Leaf.a" in t for t in _violation_texts(violations))


# --- deprecation marks and the surface name -----------------------------------------------


def test_a_removed_operation_the_baseline_deprecated_is_a_deprecated_wire_finding() -> None:
    base = _spec({"/api/fleet/widgets": {"get": {**_op(), "deprecated": True}}})
    (finding,) = classify_spec_diff(base, _spec({}))
    assert (finding.surface, finding.where, finding.deprecated) == ("wire", "/api/fleet/widgets", True)


def test_a_removed_operation_the_baseline_did_not_deprecate_is_not_deprecated() -> None:
    base = _spec({"/api/fleet/widgets": {"get": _op()}})
    (finding,) = classify_spec_diff(base, _spec({}))
    assert finding.deprecated is False


def test_a_path_is_deprecated_only_when_every_operation_on_it_is() -> None:
    base = _spec({"/api/fleet/widgets": {"get": {**_op(), "deprecated": True}, "post": _op()}})
    (finding,) = classify_spec_diff(base, _spec({}))
    assert finding.deprecated is False


def test_a_removed_deprecated_property_is_a_deprecated_finding() -> None:
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"id": {"type": "string"}, "old": {"type": "string", "deprecated": True}}},
        {"type": "object", "properties": {"id": {"type": "string"}}},
    )
    (finding,) = classify_spec_diff(base, head)
    assert (finding.where, finding.deprecated) == ("Widget.old", True)


def test_an_enum_schema_the_baseline_deprecated_marks_its_finding() -> None:
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"status": {"type": "string", "enum": ["a"], "deprecated": True}}},
        {"type": "object", "properties": {"status": {"type": "string", "enum": ["a", "b"]}}},
    )
    (finding,) = classify_spec_diff(base, head)
    assert finding.deprecated is True


def test_a_deprecated_parameter_marks_its_finding() -> None:
    def build(params: list[dict]) -> dict:
        return _spec({"/api/fleet/widgets": {"get": {**_op(), "parameters": params}}})

    old = {"name": "q", "in": "query", "required": False, "schema": {"type": "string"}, "deprecated": True}
    new = {"name": "q", "in": "query", "required": True, "schema": {"type": "string"}}
    (finding,) = classify_spec_diff(build([old]), build([new]))
    assert finding.deprecated is True


def test_an_api_only_path_is_an_http_api_finding_and_not_a_wire_one() -> None:
    base = _spec({"/api/operator/widgets": {"get": {**_op(), "deprecated": True}}})
    head = _spec({})
    assert classify_spec_diff(base, head) == []
    (finding,) = classify_api(base, head)
    assert (finding.surface, finding.where, finding.deprecated) == ("http-api", "/api/operator/widgets", True)


def test_a_wire_finding_is_reported_once_under_the_wire() -> None:
    base = _spec({"/api/fleet/widgets": {"get": _op()}})
    head = _spec({})
    wire = classify_spec_diff(base, head)
    assert len(wire) == 1
    assert classify_api(base, head, wire=wire) == []


# --- open-marked response enums (bzh:open-response-vocabulary) ----------------------------------

_OPEN = "x-blizzard-open"


def _open_ref(name: str, *, marked: bool = True) -> dict:
    return {**_ref(name), **({_OPEN: True} if marked else {})}


def _state_specs(*positions: dict, gained: bool = True) -> tuple[dict, dict]:
    """A response ``Widget`` holding each of ``positions`` as a property, the ``State`` enum gaining a value at head.

    The baseline carries ``positions`` as given; the head marks every one, so only the baseline's marks decide."""

    def spec(values: list[str], properties: dict) -> dict:
        schemas = {
            "Widget": {"type": "object", "properties": properties},
            "State": {"type": "string", "enum": values},
        }
        return _spec({"/api/fleet/widgets": {"get": _op(response_schema=_ref("Widget"))}}, schemas)

    base_props = {f"p{i}": position for i, position in enumerate(positions)}
    head_props = {f"p{i}": _marked_everywhere(position) for i, position in enumerate(positions)}
    return spec(["ready"], base_props), spec(["ready", "paused"] if gained else ["ready"], head_props)


def _marked_everywhere(node: dict) -> dict:
    """``node`` with the open mark on every ``$ref`` it holds, however nested."""
    if "$ref" in node:
        return {**node, _OPEN: True}
    marked = dict(node)
    if "anyOf" in node:
        marked["anyOf"] = [_marked_everywhere(member) for member in node["anyOf"]]
    for key in ("items", "additionalProperties"):
        if isinstance(node.get(key), dict):
            marked[key] = _marked_everywhere(node[key])
    return marked


def _gains(base: dict, head: dict) -> list[str]:
    return [t for t in _violation_texts(classify_spec_diff(base, head)) if "response enum gained value" in t]


def test_a_gain_is_additive_when_every_baseline_response_position_is_marked_open() -> None:
    base, head = _state_specs(
        _open_ref("State"),
        {"anyOf": [_open_ref("State"), {"type": "null"}]},
        {"type": "array", "items": _open_ref("State")},
        {"type": "object", "additionalProperties": _open_ref("State")},
    )
    assert classify_spec_diff(base, head) == []


def test_a_gain_is_a_finding_when_any_baseline_response_position_lacks_the_mark() -> None:
    base, head = _state_specs(_open_ref("State"), {"type": "array", "items": _open_ref("State", marked=False)})
    assert _gains(base, head) == ["State: response enum gained value(s) ['paused']"]


def test_the_head_documents_marks_do_not_open_a_baseline_position() -> None:
    base, head = _state_specs(_open_ref("State", marked=False))
    assert _gains(base, head) == ["State: response enum gained value(s) ['paused']"]


def test_an_unmarked_request_position_does_not_stop_marked_response_positions_opening_the_enum() -> None:
    base, head = _state_specs(_open_ref("State"))
    for spec in (base, head):
        spec["paths"]["/api/fleet/widgets"]["get"] = _op(
            response_schema=_ref("Widget"), request_schema={"type": "object", "properties": {"s": _ref("State")}}
        )
    assert classify_spec_diff(base, head) == []


def test_a_marked_inline_enum_gaining_a_value_is_additive_and_an_unmarked_one_is_not() -> None:
    def specs(marked: bool) -> tuple[dict, dict]:
        mark = {_OPEN: True} if marked else {}
        return _response_widget_specs(
            {"type": "object", "properties": {"status": {"type": "string", "enum": ["ready"], **mark}}},
            {"type": "object", "properties": {"status": {"type": "string", "enum": ["ready", "paused"], **mark}}},
        )

    assert classify_spec_diff(*specs(marked=True)) == []
    assert any("response enum gained value" in t for t in _violation_texts(classify_spec_diff(*specs(marked=False))))


def test_an_unmarked_inline_enum_widened_to_a_plain_string_is_breaking_and_a_marked_one_is_not() -> None:
    def specs(marked: bool) -> tuple[dict, dict]:
        mark = {_OPEN: True} if marked else {}
        return _response_widget_specs(
            {"type": "object", "properties": {"kind": {"type": "string", "enum": ["bounce"], **mark}}},
            {"type": "object", "properties": {"kind": {"type": "string"}}},
        )

    assert any(
        "response enum widened to a plain string" in t
        for t in _violation_texts(classify_spec_diff(*specs(marked=False)))
    )
    assert classify_spec_diff(*specs(marked=True)) == []


def test_a_marked_literal_moved_onto_an_enum_component_gaining_a_value_is_additive() -> None:
    color = {"type": "string", "enum": ["red", "blue"], "title": "Color"}
    base_prop = {"anyOf": [{"type": "string", "enum": ["red"], _OPEN: True}, {"type": "null"}]}
    head_prop = {"anyOf": [_open_ref("Color"), {"type": "null"}]}
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"color": base_prop}},
        {"type": "object", "properties": {"color": head_prop}},
    )
    head["components"]["schemas"]["Color"] = color
    assert classify_spec_diff(base, head) == []


def test_a_union_gaining_a_member_stays_a_finding_whatever_its_members_carry() -> None:
    extra = {"A": {"type": "object", "properties": {}}, "B": {"type": "object", "properties": {}}}
    base, head = _response_widget_specs(
        {"type": "object", "properties": {"payload": {"anyOf": [_open_ref("A")], _OPEN: True}}},
        {"type": "object", "properties": {"payload": {"anyOf": [_open_ref("A"), _open_ref("B")], _OPEN: True}}},
    )
    base["components"]["schemas"].update(extra)
    head["components"]["schemas"].update(extra)
    assert any("response union gained member" in t for t in _violation_texts(classify_spec_diff(base, head)))


class _Tone(StrEnum):
    LIGHT = "light"


class _OpenToneView(BaseModel):
    tone: Annotated[_Tone | Unrecognized, OPEN]


class _ClosedToneView(BaseModel):
    tone: _Tone


def _generated_gain(view: type[BaseModel]) -> tuple[dict, dict]:
    app = FastAPI()
    app.get("/api/fleet/tone", response_model=view)(lambda: None)
    base = app.openapi()
    head = copy.deepcopy(base)
    head["components"]["schemas"]["_Tone"]["enum"].append("dark")
    return base, head


def test_the_mark_the_open_annotation_writes_is_the_one_compat_reads() -> None:
    assert classify_spec_diff(*_generated_gain(_OpenToneView)) == []
    assert _gains(*_generated_gain(_ClosedToneView)) == ["_Tone: response enum gained value(s) ['dark']"]
