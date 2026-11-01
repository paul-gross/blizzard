"""blizzard:compat — the config, trace, egress, and store classifiers over hand-built documents,
never the committed ones."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from blizzard.tools.compat.surfaces.config import classify_config
from blizzard.tools.compat.surfaces.egress import classify_egress
from blizzard.tools.compat.surfaces.store import classify_store
from blizzard.tools.compat.surfaces.traces import classify_traces

pytestmark = pytest.mark.unit


def _reasons(findings) -> list[tuple[str, str, bool]]:
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
