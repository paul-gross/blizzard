"""What the store-baseline proof reads from a store and how it names a difference between two of them."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa

from tests.migration_support_store_migrations import _schema_snapshot

_VERSION_TABLE = "alembic_version"
_AUTOINCREMENT = re.compile(r"\bAUTOINCREMENT\b")
_PARTS = ("columns", "primary key", "foreign keys", "unique constraints", "indexes")


def schema_of(url: str, *, renamed: Mapping[str, str] | None = None) -> dict[str, object]:
    """``_schema_snapshot``'s fingerprint of the store at ``url``, with no exemption.

    ``renamed`` maps a table to the name a history renamed it from. Postgres names a primary key, a foreign key and a
    serial sequence after their table when it creates them, so a renamed table keeps the old name on all three, and a
    baseline that creates the table under its new name cannot reproduce them; that is the one class of difference
    read away, by giving the old table's generated names the new table's name."""
    engine = sa.create_engine(url)
    try:
        snapshot = _schema_snapshot(engine, exemptions={}, name_exemptions={})
    finally:
        engine.dispose()
    renamed = renamed or {}
    return {
        table: _rewrite(entry, _generated_name(renamed[table]), table) if table in renamed else entry
        for table, entry in snapshot.items()
    }


def _generated_name(table: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(table)}_")


def _rewrite(value: Any, pattern: re.Pattern[str], new: str) -> Any:
    """``value`` with every name that begins with the old table's name beginning with ``new`` instead."""
    if isinstance(value, str):
        return pattern.sub(new + "_", value)
    if isinstance(value, frozenset):
        return frozenset(_rewrite(item, pattern, new) for item in value)
    if isinstance(value, tuple):
        return tuple(_rewrite(item, pattern, new) for item in value)
    return value


def rows_of(url: str) -> dict[str, list[dict[str, Any]]]:
    """Every row of every table at ``url`` bar the version table, by table."""
    engine = sa.create_engine(url)
    try:
        metadata = sa.MetaData()
        names = [name for name in sa.inspect(engine).get_table_names() if name != _VERSION_TABLE]
        metadata.reflect(engine, only=names)
        with engine.connect() as connection:
            return {
                name: [dict(row) for row in connection.execute(sa.select(metadata.tables[name])).mappings()]
                for name in sorted(names)
            }
    finally:
        engine.dispose()


def autoincrement_of(url: str) -> dict[str, bool]:
    """Whether each table of a SQLite store at ``url`` is declared ``AUTOINCREMENT``, read from its DDL.

    ``sqlite_sequence`` is no witness: it names a table only once a row has been written to it."""
    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            ddl = connection.execute(sa.text("SELECT name, sql FROM sqlite_master WHERE type = 'table'")).all()
        return {name: bool(_AUTOINCREMENT.search(sql or "")) for name, sql in ddl if name != _VERSION_TABLE}
    finally:
        engine.dispose()


def schema_differences(history: Mapping[str, Any], baseline: Mapping[str, Any]) -> list[str]:
    """Each way ``baseline``'s fingerprint differs from ``history``'s, naming the table and the part."""
    found: list[str] = []
    for table in sorted(set(history) | set(baseline)):
        if table not in baseline:
            found.append(f"table {table}: only the history creates it")
            continue
        if table not in history:
            found.append(f"table {table}: only the baseline creates it")
            continue
        for part, kept, built in zip(_PARTS, history[table], baseline[table], strict=True):
            if kept == built:
                continue
            if isinstance(kept, frozenset) and isinstance(built, frozenset):
                found += [
                    f"table {table} {part}: only the history has {item!r}" for item in sorted(kept - built, key=repr)
                ]
                found += [
                    f"table {table} {part}: only the baseline has {item!r}" for item in sorted(built - kept, key=repr)
                ]
            else:
                found.append(f"table {table} {part}: history {kept!r}, baseline {built!r}")
    return found


def row_differences(history: Mapping[str, Any], baseline: Mapping[str, Any]) -> list[str]:
    """Each table whose normalized rows differ, with both sides."""
    return [
        f"table {table} rows: history {history.get(table)!r}, baseline {baseline.get(table)!r}"
        for table in sorted(set(history) | set(baseline))
        if history.get(table) != baseline.get(table)
    ]
