"""Helpers the ``project_columns_migration`` tests share across the revisions they prove."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.environment import EnvironmentContext
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine.interfaces import ReflectedColumn

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.migrations import MIGRATION_CLOCK_ATTRIBUTE
from blizzard.hub.store import MIGRATIONS_DIR

T0 = datetime(2026, 1, 1, tzinfo=UTC)


NOW = datetime(2026, 10, 8, 19, 0, tzinfo=UTC)


def _filler(column: ReflectedColumn) -> object:
    kind = str(column["type"]).upper()
    if "INT" in kind:
        return 1
    if "BOOL" in kind:
        return False
    if "DATE" in kind or "TIME" in kind:
        return T0
    return "x"


def insert(engine: sa.Engine, table: str, rows: list[dict[str, object]]) -> None:
    """``rows`` into ``table`` as the store holds it, each required column the row leaves out filled."""
    columns = sa.inspect(engine).get_columns(table)
    required = [c for c in columns if not c["nullable"] and c["default"] is None and not c.get("autoincrement")]
    reflected = sa.Table(table, sa.MetaData(), autoload_with=engine)
    with engine.begin() as conn:
        for row in rows:
            filled = {c["name"]: _filler(c) for c in required if c["name"] not in row}
            if "id" in filled and reflected.c.id.primary_key and isinstance(reflected.c.id.type, sa.Integer):
                del filled["id"]
            conn.execute(sa.insert(reflected).values({**filled, **row}))


def query(engine: sa.Engine, sql: str, **params: object) -> list[tuple[Any, ...]]:
    with engine.connect() as conn:
        return [tuple(row) for row in conn.execute(sa.text(sql), params)]


def run_step(conn: sa.Connection, step: Any) -> None:
    config = Config()
    config.attributes[MIGRATION_CLOCK_ATTRIBUTE] = FixedClock(NOW)
    environment = EnvironmentContext(config, ScriptDirectory(str(MIGRATIONS_DIR)))
    with Operations.context(MigrationContext.configure(conn, environment_context=environment)):
        step(conn)
