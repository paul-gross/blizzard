"""Seeding a runner store's runner-scoped tables from a reflected shape — as the previous release's statements write.

A shape reflected from a store at the revision before the runner key names no ``runner_id``, so a row built from it
writes exactly as that release did; reflected at head, the caller decides the key. Shared by the runner-scope
migration's tests and the start-up stamp's."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import sqlalchemy as sa

from blizzard.runner.store import schema

T0 = datetime(2026, 1, 1, tzinfo=UTC)

#: Every runner-scoped table but the identity row, which keys itself by its own ``runner_id``.
KEYED = frozenset(t.name for t in schema.metadata.sorted_tables if t.info["scope"] == "runner") - {"runner_identity"}

_ALLOWED_VALUES = re.compile(r"^\s*(\w+)\s+IN\s*\(\s*'([^']*)'")


def reflect(engine: sa.Engine) -> sa.MetaData:
    reflected = sa.MetaData()
    reflected.reflect(engine)
    return reflected


def _value_for(column: sa.Column) -> object:
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
        return T0
    return f"{column.name}-1"


def seed_every_keyed_table(engine: sa.Engine, shapes: sa.MetaData, *, omit: frozenset[str] = frozenset()) -> None:
    """One row in every keyed table, built from SHAPES: every column gets a value of its own type, or the first value
    its ``IN (...)`` CHECK constraint allows; a column named in ``omit`` is left out, to its default. The shapes name
    only the columns they reflected, so built from the parent's they write exactly as the previous release's statements
    do."""
    inspector = sa.inspect(engine)
    with engine.begin() as conn:
        for name in sorted(KEYED):
            table = shapes.tables[name]
            allowed = {
                match.group(1): match.group(2)
                for check in inspector.get_check_constraints(name)
                if (match := _ALLOWED_VALUES.match(check["sqltext"])) is not None
            }
            values = {c.name: allowed.get(c.name, _value_for(c)) for c in table.columns if c.name not in omit}
            conn.execute(sa.insert(table).values(values))
