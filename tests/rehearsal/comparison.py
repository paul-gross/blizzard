"""The rehearsal's comparisons — pure functions over plain data, so each is unit-tested on doctored inputs.

A view is held to the fields the baseline's OpenAPI document declares for it, and a store's seeded rows to the
baseline's columns; what the candidate adds is never compared. Each mismatch is one message naming where it is."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

type Schema = Mapping[str, Any]
type Rows = dict[str, dict[str, dict[str, str]]]

#: Chunk-view fields derived from the clock at read time, never compared.
CLOCK_DERIVED_FIELDS: frozenset[str] = frozenset()

_ABSENT = "<absent>"


def response_schema(openapi: Schema, path: str, method: str = "get") -> Schema:
    """The JSON schema of the success response ``openapi`` declares for ``method path``."""
    responses = openapi["paths"][path][method]["responses"]
    return responses["200"]["content"]["application/json"]["schema"]


def project(value: Any, schema: Schema, openapi: Schema) -> Any:
    """``value`` with every object narrowed to the properties ``schema`` declares, recursively."""
    schema = _resolved(schema, openapi)
    if isinstance(value, dict):
        for branch in schema.get("anyOf", schema.get("oneOf", ())):
            branch = _resolved(branch, openapi)
            if "properties" in branch or "additionalProperties" in branch:
                schema = branch
                break
        if "properties" in schema:
            return {
                name: project(value[name], sub, openapi) if name in value else _ABSENT
                for name, sub in schema["properties"].items()
                if name not in CLOCK_DERIVED_FIELDS
            }
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            return {key: project(item, extra, openapi) for key, item in value.items()}
        return value
    if isinstance(value, list):
        items = next(
            (_resolved(b, openapi)["items"] for b in schema.get("anyOf", ()) if "items" in _resolved(b, openapi)),
            schema.get("items", {}),
        )
        return [project(item, items, openapi) for item in value]
    return value


def view_mismatches(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    """Every field path at which a chunk's projected view differs between ``before`` and ``after``."""
    found: list[str] = []
    for chunk_id, view in before.items():
        _diff(view, after.get(chunk_id, _ABSENT), f"chunk {chunk_id}", found)
    return found


def row_losses(before: Rows, after: Rows) -> list[str]:
    """Every seeded row in ``before`` that ``after`` lost or changed, by table, key, and column."""
    found: list[str] = []
    for table, rows in sorted(before.items()):
        kept = after.get(table)
        if kept is None:
            found.append(f"table {table}: gone")
            continue
        for key, values in sorted(rows.items()):
            if key not in kept:
                found.append(f"table {table}: row {key} gone")
                continue
            for column, value in sorted(values.items()):
                now = kept[key].get(column, _ABSENT)
                if now != value:
                    found.append(f"table {table}: row {key} column {column}: {value} became {now}")
    return found


def reflection_drift(reflected: Schema, committed: Schema) -> list[str]:
    """Every table whose Postgres reflection differs from the committed document, past what the dialect alone changes.

    ``deprecated`` is left out, since reflection cannot see it."""
    left = {name: _dialect_neutral(table) for name, table in _without_deprecated(reflected["tables"]).items()}
    right = _without_deprecated(committed["tables"])
    return [
        f"table {name}: reflected {left.get(name, _ABSENT)} but committed {right.get(name, _ABSENT)}"
        for name in sorted(set(left) | set(right))
        if left.get(name) != right.get(name)
    ]


def _dialect_neutral(table: dict[str, Any]) -> dict[str, Any]:
    """Postgres reflects a Float as Double, and a lone Integer key's sequence as a server default."""
    columns = {}
    for name, column in table["columns"].items():
        column = {**column, "type": "Float" if column["type"] == "Double" else column["type"]}
        if table["primary_key"] == [name] and column["type"] == "Integer":
            column["server_default"] = False
        columns[name] = column
    return {**table, "columns": columns}


def _resolved(schema: Schema, openapi: Schema) -> Schema:
    while "$ref" in schema:
        schema = openapi["components"]["schemas"][schema["$ref"].rpartition("/")[2]]
    return schema


def _diff(before: Any, after: Any, where: str, found: list[str]) -> None:
    if isinstance(before, dict) and isinstance(after, dict):
        for name, value in before.items():
            _diff(value, after.get(name, _ABSENT), f"{where} field {name}", found)
    elif isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        for index, (left, right) in enumerate(zip(before, after, strict=True)):
            _diff(left, right, f"{where}[{index}]", found)
    elif before != after:
        found.append(f"{where}: {before!r} became {after!r}")


def _without_deprecated(node: Any) -> Any:
    if isinstance(node, dict):
        return {key: _without_deprecated(value) for key, value in node.items() if key != "deprecated"}
    if isinstance(node, list):
        return [_without_deprecated(item) for item in node]
    return node
