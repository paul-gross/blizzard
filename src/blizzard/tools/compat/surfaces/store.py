"""The store schema surface: the tables of a store's contract document, held to expand-contract.

A revision may expand — a table, a nullable column or one with a server default. Everything else is a
contraction, reported here."""

from __future__ import annotations

from typing import Any

from blizzard.tools.compat.violation import Violation

SURFACE = "store"


def _foreign_keys(table: dict[str, Any]) -> set[tuple[Any, ...]]:
    return {
        (tuple(fk["columns"]), fk["references"]["table"], tuple(fk["references"]["columns"]))
        for fk in table.get("foreign_keys", [])
    }


def _unique(table: dict[str, Any]) -> set[tuple[str, ...]]:
    return {tuple(columns) for columns in table.get("unique", [])}


def _marked(node: dict[str, Any]) -> bool:
    return bool(node.get("deprecated"))


def _column_findings(table_name: str, before: dict[str, Any], after: dict[str, Any]) -> list[Violation]:
    findings: list[Violation] = []
    head_columns = after.get("columns", {})
    for name, column in before.get("columns", {}).items():
        where = f"{table_name}.{name}"
        deprecated = _marked(column) or _marked(before)
        now = head_columns.get(name)
        if now is None:
            findings.append(Violation(SURFACE, where, "column dropped", deprecated))
            continue
        if column.get("type") != now.get("type"):
            reason = f"column type changed from {column.get('type')} to {now.get('type')}"
            findings.append(Violation(SURFACE, where, reason, deprecated))
        if column.get("nullable") and not now.get("nullable") and not now.get("server_default"):
            findings.append(Violation(SURFACE, where, "column made NOT NULL without a server default", deprecated))
    for name, column in head_columns.items():
        if name not in before.get("columns", {}) and not column.get("nullable") and not column.get("server_default"):
            findings.append(
                Violation(SURFACE, f"{table_name}.{name}", "NOT NULL column added without a server default")
            )
    return findings


def classify_store(base: dict[str, Any], head: dict[str, Any]) -> list[Violation]:
    """Every contraction between two store documents."""
    findings: list[Violation] = []
    head_tables = head.get("tables", {})
    for name, before in base.get("tables", {}).items():
        deprecated = _marked(before)
        after = head_tables.get(name)
        if after is None:
            findings.append(Violation(SURFACE, name, "table dropped", deprecated))
            continue
        findings.extend(_column_findings(name, before, after))
        if _unique(after) - _unique(before):
            findings.append(Violation(SURFACE, name, "unique constraint added to an existing table", deprecated))
        if set(after.get("checks", [])) - set(before.get("checks", [])):
            findings.append(Violation(SURFACE, name, "check constraint added to an existing table", deprecated))
        if before.get("primary_key") != after.get("primary_key"):
            findings.append(Violation(SURFACE, name, "primary key changed", deprecated))
        if _foreign_keys(before) != _foreign_keys(after):
            findings.append(Violation(SURFACE, name, "foreign key changed", deprecated))
    return findings
