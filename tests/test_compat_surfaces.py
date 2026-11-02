"""blizzard:compat — the config, trace, egress, and store classifiers over hand-built documents,
never the committed ones."""

from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any

import pytest

from blizzard.tools.compat.surfaces.cli import classify_cli
from blizzard.tools.compat.surfaces.config import classify_config
from blizzard.tools.compat.surfaces.egress import classify_egress
from blizzard.tools.compat.surfaces.sse import classify_sse
from blizzard.tools.compat.surfaces.store import classify_store
from blizzard.tools.compat.surfaces.traces import classify_traces
from blizzard.tools.compat.violation import Violation

pytestmark = pytest.mark.unit


def _reasons(findings: Iterable[Violation]) -> list[tuple[str, str, bool]]:
    return [(f.where, f.reason, f.deprecated) for f in findings]


# --- configuration -----------------------------------------------------------------------


def _key(path: str, type: str = "string", *, required: bool = False, deprecated: bool = False) -> dict[str, Any]:
    return {
        "path": path,
        "type": type,
        "required": required,
        "default": None,
        "deprecated": deprecated,
        "replaced_by": None,
    }


def _config(*keys: dict[str, Any], paths: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"daemon": "hub", "keys": list(keys), "paths": [{"path": p, "holds": "x"} for p in paths]}


def test_a_removed_config_key_is_a_finding_carrying_the_baseline_mark() -> None:
    base = _config(_key("a"), _key("b", deprecated=True))
    findings = classify_config(base, _config(_key("a")))
    assert _reasons(findings) == [("b", "configuration key removed", True)]
    assert findings[0].surface == "config"


def test_a_config_key_whose_type_changed_is_a_finding() -> None:
    findings = classify_config(_config(_key("a", "string")), _config(_key("a", "integer")))
    assert _reasons(findings) == [("a", "configuration key type changed from string to integer", False)]


def test_a_config_key_that_became_required_is_a_finding() -> None:
    assert _reasons(classify_config(_config(_key("a")), _config(_key("a", required=True)))) == [
        ("a", "configuration key became required", False)
    ]


def test_a_new_required_config_key_is_a_finding() -> None:
    assert _reasons(classify_config(_config(), _config(_key("a", required=True)))) == [
        ("a", "configuration key became required", False)
    ]


def test_a_removed_durable_path_is_a_finding() -> None:
    assert _reasons(classify_config(_config(paths=("data/x",)), _config())) == [
        ("data/x", "durable path removed", False)
    ]


def test_a_new_optional_key_and_a_new_path_are_additive() -> None:
    assert classify_config(_config(), _config(_key("a"), paths=("data/x",))) == []


def test_the_keys_inside_the_flags_table_are_never_findings() -> None:
    base = _config(_key("flags", "open table"), _key("flags.old"))
    head = _config(_key("flags", "open table"), _key("flags.new", required=True))
    assert classify_config(base, head) == []


def test_the_flags_entry_itself_is_diffed() -> None:
    assert _reasons(classify_config(_config(_key("flags", "open table")), _config())) == [
        ("flags", "configuration key removed", False)
    ]


# --- traces ------------------------------------------------------------------------------


def _traces(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "spans": [{"name": "step <node>", "role": "step"}],
        "event_names": ["bounce"],
        "attributes": [{"name": "blizzard.chunk.id", "type": "string"}],
        "resource_attributes": [{"name": "service.name", "type": "string"}],
        "platform": {"attributes": [{"name": "blizzard.caller", "type": "string"}]},
    }
    document.update(overrides)
    return document


def test_a_removed_span_is_a_finding() -> None:
    assert _reasons(classify_traces(_traces(), _traces(spans=[]))) == [("step <node>", "span removed", False)]


def test_a_removed_deprecated_span_carries_the_mark() -> None:
    base = _traces(spans=[{"name": "old", "deprecated": True}])
    assert _reasons(classify_traces(base, _traces(spans=[]))) == [("old", "span removed", True)]


def test_a_removed_event_is_a_finding() -> None:
    assert _reasons(classify_traces(_traces(), _traces(event_names=[]))) == [("bounce", "event removed", False)]


def test_a_removed_event_written_as_an_entry_carries_its_mark() -> None:
    base = _traces(event_names=[{"name": "bounce", "deprecated": True}])
    assert _reasons(classify_traces(base, _traces(event_names=[]))) == [("bounce", "event removed", True)]


def test_a_removed_attribute_is_a_finding() -> None:
    findings = classify_traces(_traces(), _traces(attributes=[]))
    assert _reasons(findings) == [("blizzard.chunk.id", "attribute removed", False)]


def test_a_retyped_attribute_is_a_finding() -> None:
    head = _traces(attributes=[{"name": "blizzard.chunk.id", "type": "int"}])
    assert _reasons(classify_traces(_traces(), head)) == [
        ("blizzard.chunk.id", "attribute type changed from string to int", False)
    ]


def test_a_removed_resource_or_platform_attribute_is_a_finding() -> None:
    head = _traces(resource_attributes=[], platform={"attributes": []})
    assert {r[1] for r in _reasons(classify_traces(_traces(), head))} == {
        "resource attribute removed",
        "platform attribute removed",
    }


def test_added_trace_entries_and_a_new_deprecation_mark_are_additive() -> None:
    head = _traces(
        spans=[{"name": "step <node>", "deprecated": True}, {"name": "new"}],
        event_names=["bounce", "other"],
        attributes=[{"name": "blizzard.chunk.id", "type": "string"}, {"name": "x", "type": "int"}],
    )
    assert classify_traces(_traces(), head) == []


# --- egress ------------------------------------------------------------------------------


def _column(name: str, type: str = "string", *, nullable: bool = False, **extra: Any) -> dict[str, Any]:
    return {"name": name, "type": type, "nullable": nullable, **extra}


def _egress(*columns: dict[str, Any], major: int = 1, **dataset: Any) -> dict[str, Any]:
    return {"datasets": [{"name": "steps", "major_version": major, "columns": list(columns), **dataset}]}


def test_a_removed_egress_column_is_a_finding() -> None:
    base = _egress(_column("a"), _column("b", deprecated=True))
    assert _reasons(classify_egress(base, _egress(_column("a")))) == [("steps v1.b", "column removed", True)]


def test_a_retyped_egress_column_is_a_finding() -> None:
    findings = classify_egress(_egress(_column("a")), _egress(_column("a", "int")))
    assert _reasons(findings) == [("steps v1.a", "column type changed from string to int", False)]


def test_a_nullable_egress_column_made_required_is_a_finding() -> None:
    findings = classify_egress(_egress(_column("a", nullable=True)), _egress(_column("a")))
    assert _reasons(findings) == [("steps v1.a", "nullable column became required", False)]


def test_a_dataset_major_no_longer_written_is_a_finding() -> None:
    assert _reasons(classify_egress(_egress(_column("a")), {"datasets": []})) == [
        ("steps v1", "dataset major no longer written", False)
    ]


def test_a_deprecated_dataset_major_dropped_carries_the_mark() -> None:
    findings = classify_egress(_egress(_column("a"), deprecated=True), {"datasets": []})
    assert findings[0].deprecated is True


def test_a_new_major_beside_the_old_and_a_new_column_are_additive() -> None:
    head = copy.deepcopy(_egress(_column("a"), _column("new", nullable=True)))
    head["datasets"].append({"name": "steps", "major_version": 2, "columns": [_column("a")]})
    assert classify_egress(_egress(_column("a")), head) == []


# --- store -------------------------------------------------------------------------------


def _col(
    type: str = "String", *, nullable: bool = False, server_default: bool = False, deprecated: Any = None
) -> dict[str, object]:
    return {"type": type, "nullable": nullable, "server_default": server_default, "deprecated": deprecated}


def _table(columns: dict[str, dict[str, object]], **extra: Any) -> dict[str, Any]:
    table: dict[str, Any] = {
        "columns": columns,
        "primary_key": ["id"],
        "unique": [],
        "checks": [],
        "foreign_keys": [],
        "deprecated": None,
    }
    table.update(extra)
    return table


def _store(**tables: dict[str, Any]) -> dict[str, Any]:
    return {"store": "hub", "head_revision": "r1", "tables": tables}


_BASE = _store(t=_table({"id": _col(), "n": _col(nullable=True)}))


def _head(**changes: Any) -> dict[str, Any]:
    head = copy.deepcopy(_BASE)
    head["tables"]["t"].update(changes)
    return head


def test_a_dropped_table_is_a_finding_and_carries_the_mark() -> None:
    base = _store(t=_table({"id": _col()}, deprecated="0.3"))
    assert _reasons(classify_store(base, _store())) == [("t", "table dropped", True)]


def test_a_dropped_column_is_a_finding() -> None:
    head = _head(columns={"id": _col()})
    assert _reasons(classify_store(_BASE, head)) == [("t.n", "column dropped", False)]


def test_a_dropped_deprecated_column_carries_the_mark() -> None:
    base = _store(t=_table({"id": _col(), "n": _col(nullable=True, deprecated="0.3")}))
    assert _reasons(classify_store(base, _store(t=_table({"id": _col()}))))[0][2] is True


def test_a_retyped_column_is_a_finding() -> None:
    head = _head(columns={"id": _col(), "n": _col("Integer", nullable=True)})
    assert _reasons(classify_store(_BASE, head)) == [("t.n", "column type changed from String to Integer", False)]


def test_a_column_made_not_null_without_a_default_is_a_finding() -> None:
    head = _head(columns={"id": _col(), "n": _col()})
    assert _reasons(classify_store(_BASE, head)) == [("t.n", "column made NOT NULL without a server default", False)]


def test_a_column_made_not_null_with_a_server_default_is_additive() -> None:
    assert classify_store(_BASE, _head(columns={"id": _col(), "n": _col(server_default=True)})) == []


def test_a_not_null_column_added_without_a_default_is_a_finding() -> None:
    head = _head(columns={"id": _col(), "n": _col(nullable=True), "m": _col()})
    assert _reasons(classify_store(_BASE, head)) == [("t.m", "NOT NULL column added without a server default", False)]


def test_a_unique_constraint_added_to_an_existing_table_is_a_finding() -> None:
    assert _reasons(classify_store(_BASE, _head(unique=[["n"]]))) == [
        ("t", "unique constraint added to an existing table", False)
    ]


def test_a_check_constraint_added_to_an_existing_table_is_a_finding() -> None:
    assert _reasons(classify_store(_BASE, _head(checks=["ck_t"]))) == [
        ("t", "check constraint added to an existing table", False)
    ]


def test_a_changed_primary_key_is_a_finding() -> None:
    assert _reasons(classify_store(_BASE, _head(primary_key=["id", "n"]))) == [("t", "primary key changed", False)]


def test_a_changed_foreign_key_is_a_finding() -> None:
    fk = {"columns": ["n"], "references": {"table": "u", "columns": ["id"]}}
    assert _reasons(classify_store(_BASE, _head(foreign_keys=[fk]))) == [("t", "foreign key changed", False)]


def test_expansions_are_additive() -> None:
    head = _head(
        columns={"id": _col(), "n": _col(nullable=True), "m": _col(nullable=True), "d": _col(server_default=True)}
    )
    head["tables"]["new"] = _table({"id": _col()}, unique=[["id"]], checks=["ck"])
    assert classify_store(_BASE, head) == []


def test_a_new_deprecation_mark_is_additive() -> None:
    assert classify_store(_BASE, _head(deprecated="0.3")) == []


# --- event stream -------------------------------------------------------------------------


def _stream(
    kinds: dict[str, dict[str, Any]], *, deprecated: tuple[str, ...] = (), scope: str = "hub"
) -> dict[str, Any]:
    """A composed scope: each kind's cases as ``{case: {field: value}}``."""
    manifest: dict[str, Any] = {"kinds": list(kinds)}
    if deprecated:
        manifest["deprecated"] = list(deprecated)
    return {"scope": scope, "manifest": manifest, "cases": kinds}


def test_a_removed_kind_is_a_break() -> None:
    base = _stream({"a-changed": {"c": {"x": 1}}, "b-changed": {"c": {"x": 1}}})
    head = _stream({"a-changed": {"c": {"x": 1}}})
    assert _reasons(classify_sse(base, head)) == [("hub b-changed", "frame kind removed", False)]


def test_a_field_kept_by_any_case_is_not_removed() -> None:
    base = _stream({"a-changed": {"minimal": {"x": 1}, "full": {"x": 1, "y": 2}}})
    head = _stream({"a-changed": {"full": {"x": 1, "y": 2}}})
    assert classify_sse(base, head) == []


def test_a_field_missing_from_every_case_is_a_break_in_its_scope() -> None:
    base = _stream({"a-changed": {"minimal": {"x": 1}, "full": {"x": 1, "y": 2}}}, scope="runner")
    head = _stream({"a-changed": {"minimal": {"x": 1}, "full": {"x": 1}}}, scope="runner")
    assert _reasons(classify_sse(base, head)) == [("runner a-changed.y", "frame field removed", False)]


def test_an_added_kind_or_field_is_additive() -> None:
    base = _stream({"a-changed": {"c": {"x": 1}}})
    head = _stream({"a-changed": {"c": {"x": 1, "y": 2}}, "b-changed": {"c": {"z": 1}}})
    assert classify_sse(base, head) == []


def test_a_kind_or_field_the_base_manifest_marks_is_deprecated() -> None:
    base = _stream(
        {"a-changed": {"c": {"x": 1, "y": 2}}, "b-changed": {"c": {"z": 1}}}, deprecated=("a-changed.y", "b-changed")
    )
    head = _stream({"a-changed": {"c": {"x": 1}}})
    assert _reasons(classify_sse(base, head)) == [
        ("hub a-changed.y", "frame field removed", True),
        ("hub b-changed", "frame kind removed", True),
    ]


# --- CLI ----------------------------------------------------------------------------------


def _param(
    name: str, kind: str = "option", *, type: str = "text", required: bool = False, **extra: Any
) -> dict[str, Any]:
    opts = [f"--{name}"] if kind == "option" else [name]
    return {"name": name, "kind": kind, "opts": opts, "secondary_opts": [], "type": type, "required": required, **extra}


def _node(
    path: str, *params: dict[str, Any], commands: dict[str, dict[str, Any]] | None = None, **extra: Any
) -> dict[str, Any]:
    node: dict[str, Any] = {"path": path, "params": list(params), **extra}
    if commands is not None:
        node["commands"] = commands
    return node


def _tree(*commands: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return _node("hub", commands={c["path"].split()[-1]: c for c in commands}, **extra)


def test_a_removed_command_is_a_break() -> None:
    base = _tree(_node("hub a"), _node("hub b"))
    assert _reasons(classify_cli(base, _tree(_node("hub a")))) == [("hub b", "command removed", False)]


def test_a_new_command_and_a_new_optional_parameter_are_additive() -> None:
    base = _tree(_node("hub a", _param("x", "argument", required=True)))
    head = _tree(
        _node("hub a", _param("x", "argument", required=True), _param("y", "argument"), _param("verbose")),
        _node("hub b"),
    )
    assert classify_cli(base, head) == []


def test_a_removed_option_spelling_is_a_break_and_a_kept_one_is_not() -> None:
    base = _tree(_node("hub a", {**_param("json"), "opts": ["--json", "-j"]}))
    head = _tree(_node("hub a", {**_param("json"), "opts": ["--json"]}))
    assert _reasons(classify_cli(base, head)) == [("hub a -j", "option removed", False)]


def test_a_removed_argument_position_is_a_break_but_a_renamed_argument_is_not() -> None:
    base = _tree(_node("hub a", _param("one", "argument"), _param("two", "argument")))
    assert classify_cli(base, _tree(_node("hub a", _param("uno", "argument"), _param("dos", "argument")))) == []
    assert _reasons(classify_cli(base, _tree(_node("hub a", _param("one", "argument"))))) == [
        ("hub a <argument 2>", "argument removed", False)
    ]


def test_an_option_or_argument_that_became_required_is_a_break() -> None:
    base = _tree(_node("hub a", _param("x"), _param("y", "argument")))
    head = _tree(_node("hub a", _param("x", required=True), _param("y", "argument", required=True)))
    assert _reasons(classify_cli(base, head)) == [
        ("hub a --x", "option became required", False),
        ("hub a <argument 1>", "argument became required", False),
    ]


def test_a_new_required_option_or_argument_is_a_break() -> None:
    base = _tree(_node("hub a"))
    head = _tree(_node("hub a", _param("x", required=True), _param("y", "argument", required=True)))
    assert _reasons(classify_cli(base, head)) == [
        ("hub a --x", "required option added", False),
        ("hub a <argument 1>", "required argument added", False),
    ]


def test_a_changed_type_is_a_break() -> None:
    base = _tree(_node("hub a", _param("x", type="text")))
    head = _tree(_node("hub a", _param("x", type="integer")))
    assert _reasons(classify_cli(base, head)) == [("hub a --x", "option type changed from text to integer", False)]


def test_a_renamed_to_get_verb_resolves_through_the_groups_alias() -> None:
    base = _node(
        "hub", commands={"w": _node("hub w", commands={"show": _node("hub w show", _param("id", "argument"))})}
    )
    head = _node(
        "hub",
        commands={
            "w": _node(
                "hub w", aliases=["show", "read"], commands={"get": _node("hub w get", _param("id", "argument"))}
            )
        },
    )
    assert classify_cli(base, head) == []
    without = _node("hub", commands={"w": _node("hub w", aliases=[], commands={"get": _node("hub w get")})})
    assert _reasons(classify_cli(base, without)) == [("hub w show", "command removed", False)]


def test_a_removed_alias_is_a_break() -> None:
    base = _node("hub", commands={"w": _node("hub w", aliases=["show", "read"], commands={"get": _node("hub w get")})})
    head = _node("hub", commands={"w": _node("hub w", aliases=["show"], commands={"get": _node("hub w get")})})
    assert _reasons(classify_cli(base, head)) == [("hub w read", "command alias removed", False)]


def test_deprecation_is_read_from_the_command_an_ancestor_group_or_the_option() -> None:
    base = _tree(
        _node("hub a", _param("x", deprecated=True), deprecated=True),
        _node("hub g", commands={"c": _node("hub g c")}, deprecated=True),
        _node("hub b", _param("y", deprecated=True), _param("z")),
    )
    head = _tree(_node("hub a"), _node("hub g", commands={}), _node("hub b", _param("z")))
    assert _reasons(classify_cli(base, head)) == [
        ("hub a --x", "option removed", True),
        ("hub g c", "command removed", True),
        ("hub b --y", "option removed", True),
    ]
