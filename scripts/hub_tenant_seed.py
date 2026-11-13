"""Seed one tenant with a row in every table it owns, derived from the hub schema.

The drain proof needs a tenant whose rows span the whole schema under enforced foreign keys. The seed is walked parents
first (``metadata.sorted_tables``); each foreign key points at the parent's seeded row and every other required column
is filled by type, so a table added later seeds itself unless a CHECK or the scoped seam demands a value, which
:data:`OVERRIDES` names.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy import Column, Connection, Table

from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.tenant_teardown_store import DRAIN_ORDER
from blizzard.hub.store.scoped import ScopedConnections, is_tenant_owned

_MOMENT = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)

#: Columns a CHECK constraint or the scoped seam constrains beyond their type, by table.
OVERRIDES: dict[str, dict[str, Any]] = {
    "findings": {"source": "review"},
    "garden_proposals": {"origin": "operator", "created_by": "seed"},
}


def _value(column: Column[Any]) -> Any:
    kind: Any = column.type
    python: type[Any]
    while True:
        try:
            python = kind.python_type
            break
        except NotImplementedError:
            kind = kind.impl  # A TypeDecorator names its python type through its impl.
    if issubclass(python, bool):
        return False
    if issubclass(python, int):
        return 1
    if issubclass(python, float):
        return 1.0
    if issubclass(python, dt.datetime):
        return _MOMENT
    if issubclass(python, bytes):
        return b"seed"
    return "seed"


_IN_LIST = re.compile(r"(\w+)\s+IN\s*\(\s*'([^']*)'")


def _allowed(table: Table) -> dict[str, str]:
    """The first listed value of each ``col IN ('a', 'b')`` CHECK on ``table``."""
    found: dict[str, str] = {}
    for constraint in table.constraints:
        if isinstance(constraint, sa.CheckConstraint):
            for column, value in _IN_LIST.findall(str(constraint.sqltext)):
                found.setdefault(column, value)
    return found


def _tables_to_seed() -> list[Table]:
    """The tenant-owned tables and every table a foreign key of theirs reaches, parents first."""
    needed: set[str] = set()

    def reach(table: Table) -> None:
        if table.name in needed or table is s.tenants:
            return
        needed.add(table.name)
        for fk in table.foreign_key_constraints:
            reach(fk.referred_table)

    for table in DRAIN_ORDER:
        reach(table)
    return [t for t in s.metadata.sorted_tables if t.name in needed]


def seed_tenant(connections: ScopedConnections, conn: Connection) -> dict[str, dict[str, Any]]:
    """Insert one row into every table the tenant owns, through the scoped seam; the rows by table name."""
    seeded: dict[str, dict[str, Any]] = {}
    tenant = connections.scope.tenant
    for table in _tables_to_seed():
        row: dict[str, Any] = {}
        for column in table.columns:
            if column.name == "tenant_id":
                continue
            if column.autoincrement is True or (column.primary_key and isinstance(column.type, sa.Integer)):
                continue
            row[column.key] = None if column.nullable else _value(column)
        for fk in table.foreign_key_constraints:
            parent = fk.referred_table
            if parent is table:
                continue
            parent_row = seeded.get(parent.name, {})
            for element in fk.elements:
                if element.parent.name == "tenant_id":
                    continue
                row[element.parent.key] = parent_row[element.column.key]
        row.update(_allowed(table))
        row.update(OVERRIDES.get(table.name, {}))
        if is_tenant_owned(table):
            result = conn.execute(connections.insert(table).values(row))
        else:
            result = conn.execute(sa.insert(table).values(row))
        generated = cast("Any", result.inserted_primary_key)
        for name, value in generated._mapping.items():
            if row.get(name) is None:
                row[str(name)] = value
        row["tenant_id"] = tenant
        seeded[table.name] = row
    return seeded
