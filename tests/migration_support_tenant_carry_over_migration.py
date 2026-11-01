"""Helpers the ``tenant_carry_over_migration`` tests share across the revisions they prove."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import event

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store.schema import GLOBAL_TABLES

_BEFORE = "20261008_1000_drop_work_item_proposals"


_TENANTS = "20261008_1100_tenants"


_BACKFILL = "20261008_1200_tenant_id_backfill"


_T0 = datetime(2026, 1, 1, tzinfo=UTC)


_SEED = (
    "INSERT INTO graphs (graph_id, name, entry_node_id, definition_yaml, created_at)"
    " VALUES ('gr_1', 'g', 'nd_1', '', :at)",
    "INSERT INTO chunks (chunk_id, graph_id, minted_at) VALUES ('ch_1', 'gr_1', :at)",
    "INSERT INTO chunks (chunk_id, graph_id, minted_at) VALUES ('ch_2', 'gr_1', :at)",
    "INSERT INTO users (id, username, display_name, email, role, created_at)"
    " VALUES ('usr_1', 'ada', 'Ada', 'ada@example.com', 'admin', :at)",
    "INSERT INTO event_log (recorded_at, severity, kind, chunk_id, message)"
    " VALUES (:at, 'info', 'chunk-minted', 'ch_1', 'minted')",
    "INSERT INTO event_log (recorded_at, severity, kind, message)"
    " VALUES (:at, 'warning', 'trace-export-failed', 'export failed')",
)


def _store(tmp_path: Path) -> tuple[str, sa.Engine]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    return db_url, create_engine_from_url(db_url)


def _tables(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names()) - {"alembic_version"}


def _row_counts(engine: sa.Engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            table: conn.execute(sa.select(sa.func.count()).select_from(sa.table(table))).scalar_one()
            for table in _tables(engine)
        }


def _tenant_rows(engine: sa.Engine) -> list[sa.Row]:
    with engine.connect() as conn:
        return list(conn.execute(sa.text("SELECT tenant_id, name, created_at, created_by FROM tenants")).all())


def _schema(engine: sa.Engine) -> dict[str, object]:
    inspector = sa.inspect(engine)
    return {
        table: (
            frozenset((c["name"], str(c["type"]), c["nullable"]) for c in inspector.get_columns(table)),
            frozenset((tuple(i["column_names"]), i["unique"], i["name"]) for i in inspector.get_indexes(table)),
            frozenset(tuple(u["column_names"]) for u in inspector.get_unique_constraints(table)),
            frozenset(
                (tuple(fk["constrained_columns"]), fk["referred_table"]) for fk in inspector.get_foreign_keys(table)
            ),
        )
        for table in _tables(engine)
    }


_ALLOWED_VALUES = re.compile(r"^\s*(\w+)\s+IN\s*\(\s*'([^']*)'")

#: The forge addresses a seeded row holds: an origin each, as a store any later revision carries over holds them.
_ADDRESSES = {
    ("repositories", "forge_api_url"): "https://api.github.com",
    ("work_sources", "api_base"): "https://api.github.com",
    ("work_sources", "web_base"): "https://github.com",
}


def _seed_every_tenant_owned_table(engine: sa.Engine) -> set[str]:
    """One row in every table outside the global list, built from the store's reflected shape: every
    column without a server default gets a value of its own type, or the first value its ``IN (...)``
    CHECK constraint allows; filling every column satisfies the "this column implies that one" checks. A foreign-key
    column takes its parent column's value, so every link names the row the seed puts in its parent."""
    reflected = sa.MetaData()
    reflected.reflect(engine)
    inspector = sa.inspect(engine)
    seeded: set[str] = set()
    with engine.begin() as conn:
        for table in reflected.sorted_tables:
            if table.name in GLOBAL_TABLES or table.name == "alembic_version":
                continue
            allowed = {
                match.group(1): match.group(2)
                for check in inspector.get_check_constraints(table.name)
                if (match := _ALLOWED_VALUES.match(check["sqltext"])) is not None
            }
            row = {
                column.name: _ADDRESSES.get((table.name, column.name), allowed.get(column.name, _value_for(column)))
                for column in table.columns
                if column.server_default is None
            }
            conn.execute(sa.insert(table).values(row))
            seeded.add(table.name)
    return seeded


def _value_for(column: sa.Column) -> object:
    if column.foreign_keys:
        return _value_for(next(iter(column.foreign_keys)).column)
    try:
        python_type = column.type.python_type
    except NotImplementedError:
        return f"{column.name}-1"
    if python_type is bool:
        return False
    if python_type is int:
        return 1
    if python_type is float:
        return 1.0
    if python_type is datetime:
        return _T0
    if python_type is bytes:
        return b"1"
    return f"{column.name}-1"


def _keyed_tables(engine: sa.Engine) -> dict[str, bool]:
    """Every table outside the global list, and whether it carries ``tenant_id``."""
    inspector = sa.inspect(engine)
    return {
        table: any(column["name"] == "tenant_id" for column in inspector.get_columns(table))
        for table in _tables(engine) - GLOBAL_TABLES
    }


def _tenants_of(engine: sa.Engine, table: str) -> list[str | None]:
    with engine.connect() as conn:
        return list(conn.execute(sa.select(sa.column("tenant_id")).select_from(sa.table(table))).scalars())


def _foreign_key_violations(engine: sa.Engine) -> list[tuple[object, ...]]:
    """Every row whose foreign key names no row of its parent, as SQLite's own check reports it."""
    with engine.connect() as conn:
        return [tuple(row) for row in conn.exec_driver_sql("PRAGMA foreign_key_check")]


_LINKS_AT_HEAD = {
    **{
        table: f"SELECT c.{key}, s.slug FROM {table} c"
        f" JOIN scopes s ON s.tenant_id = c.tenant_id AND s.scope_id = c.{scope_id}"
        for table, key, scope_id in (
            ("scope_lifecycle_facts", "id", "scope_id"),
            ("routines", "routine_id", "default_scope_id"),
            ("routine_scopes", "routine_id", "scope_id"),
            ("work_item_runs", "work_item_id", "scope_id"),
            ("findings", "finding_id", "scope_id"),
            ("finding_sets", "finding_set_id", "scope_id"),
        )
    },
    **{
        table: f"SELECT c.{key}, s.name FROM {table} c"
        f" JOIN secrets s ON s.tenant_id = c.tenant_id AND s.name = c.{name}"
        for table, key, name in (
            ("secret_lifecycle_facts", "id", "name"),
            ("work_sources", "name", "secret_name"),
            ("repositories", "name", "secret_name"),
        )
    },
    "question_answers": "SELECT q.question_id, a.answer FROM question_answers a"
    " JOIN questions q ON q.tenant_id = a.tenant_id AND q.question_id = a.question_id",
    "answer_deliveries": "SELECT d.id, q.question_id FROM answer_deliveries d"
    " JOIN questions q ON q.tenant_id = d.tenant_id AND q.question_id = d.question_id",
    "keyed_locks": "SELECT namespace, key FROM keyed_locks",
    "work_item_sequence": "SELECT source, next_ref FROM work_item_sequence",
    "work_items": "SELECT source, ref FROM work_items",
}


@contextmanager
def _rows_each_update_changed() -> Iterator[list[tuple[str, int]]]:
    """Every ``UPDATE`` any engine in the process runs while the context is open — the migration runner's engine
    included — as the table it names and the rows it changed, in issue order."""
    updates: list[tuple[str, int]] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        words = statement.split()
        if words[0].upper() == "UPDATE":
            updates.append((words[1], cursor.rowcount))

    event.listen(sa.Engine, "after_cursor_execute", record)
    try:
        yield updates
    finally:
        event.remove(sa.Engine, "after_cursor_execute", record)
