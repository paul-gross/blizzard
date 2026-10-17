"""Build one store's schema document (``contracts/store/<store>.json``).

Two producers share one normalization: ``document_from_metadata`` reads a ``schema.py``
``MetaData`` plus the tree's head revision, and ``document_from_engine`` reflects a live
engine plus its applied revision. The committed document is the first; the reflection
test holds it to the second. Every rule below is one a SQLite reflection, and a later
Postgres one, can reproduce:

* a column's type is the class name of its generic type (a ``TypeDecorator`` is unwrapped
  to its ``impl`` first);
* a server default is recorded by presence, never by value, since the same Boolean default
  renders ``0`` on SQLite and ``false`` on Postgres;
* primary keys, unique constraints and foreign keys are identified by their columns and
  referents, never by name, since dialects name unnamed constraints;
* a check constraint is identified by name alone, since its SQL text differs across dialects;
* indexes, column order and ``alembic_version`` are left out.

``info={"deprecated": "<MAJOR.MINOR>"}`` on a table or column surfaces as ``deprecated``;
reflection cannot see it, so it reads ``None`` there."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import CheckConstraint, Column, Engine, MetaData, Table, UniqueConstraint, inspect
from sqlalchemy.types import TypeDecorator, TypeEngine

_VERSION_TABLE = "alembic_version"

type Document = dict[str, Any]


def document_from_metadata(store: str, metadata: MetaData, head_revision: str | None) -> Document:
    """The document a ``schema.py`` ``MetaData`` declares."""
    tables = {table.name: _declared_table(table) for table in metadata.tables.values()}
    return _document(store, head_revision, tables)


def document_from_engine(store: str, engine: Engine, applied_revision: str | None) -> Document:
    """The document a live engine's reflection yields."""
    inspector = inspect(engine)
    tables = {name: _reflected_table(inspector, name) for name in inspector.get_table_names() if name != _VERSION_TABLE}
    return _document(store, applied_revision, tables)


def _document(store: str, head_revision: str | None, tables: Mapping[str, Document]) -> Document:
    return {"store": store, "head_revision": head_revision, "tables": dict(sorted(tables.items()))}


def _portable_type(column_type: TypeEngine[Any]) -> str:
    if isinstance(column_type, TypeDecorator):
        column_type = column_type.impl_instance
    return type(column_type.as_generic()).__name__


def _column(portable_type: str, *, nullable: bool, has_server_default: bool, deprecated: str | None) -> Document:
    return {
        "type": portable_type,
        "nullable": nullable,
        "server_default": has_server_default,
        "deprecated": deprecated,
    }


def _table(
    columns: Mapping[str, Document],
    *,
    primary_key: list[str],
    unique: list[list[str]],
    foreign_keys: list[Document],
    checks: list[str],
    deprecated: str | None,
) -> Document:
    return {
        "columns": dict(sorted(columns.items())),
        "primary_key": primary_key,
        "unique": sorted(unique),
        "foreign_keys": sorted(foreign_keys, key=lambda fk: (fk["columns"], fk["references"]["table"])),
        "checks": sorted(checks),
        "deprecated": deprecated,
    }


def _foreign_key(columns: list[str], table: str, referred: list[str]) -> Document:
    return {"columns": columns, "references": {"table": table, "columns": referred}}


def _declared_column(column: Column[Any]) -> Document:
    return _column(
        _portable_type(column.type),
        nullable=bool(column.nullable),
        has_server_default=column.server_default is not None,
        deprecated=column.info.get("deprecated"),
    )


def _declared_table(table: Table) -> Document:
    checks: list[str] = []
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint):
            if constraint.name is None:
                raise ValueError(f"{table.name}: a check constraint must be named to appear in the store contract")
            checks.append(str(constraint.name))
    return _table(
        {column.name: _declared_column(column) for column in table.columns},
        primary_key=[column.name for column in table.primary_key.columns],
        unique=[
            [column.name for column in constraint.columns]
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        ],
        foreign_keys=[
            _foreign_key(
                [column.name for column in constraint.columns],
                constraint.referred_table.name,
                [element.column.name for element in constraint.elements],
            )
            for constraint in table.foreign_key_constraints
        ],
        checks=checks,
        deprecated=table.info.get("deprecated"),
    )


def _reflected_table(inspector: Any, name: str) -> Document:
    return _table(
        {
            column["name"]: _column(
                _portable_type(column["type"]),
                nullable=bool(column["nullable"]),
                has_server_default=column["default"] is not None,
                deprecated=None,
            )
            for column in inspector.get_columns(name)
        },
        primary_key=list(inspector.get_pk_constraint(name)["constrained_columns"]),
        unique=[list(unique["column_names"]) for unique in inspector.get_unique_constraints(name)],
        foreign_keys=[
            _foreign_key(list(fk["constrained_columns"]), fk["referred_table"], list(fk["referred_columns"]))
            for fk in inspector.get_foreign_keys(name)
        ],
        checks=[check["name"] for check in inspector.get_check_constraints(name)],
        deprecated=None,
    )
