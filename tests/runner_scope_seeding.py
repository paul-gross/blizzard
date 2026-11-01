"""Seeding a runner store's runner-scoped tables from a reflected shape — as the previous release's statements write.

A shape reflected from a store at the revision before the runner key names no ``runner_id``, so a row built from it
writes exactly as that release did; reflected at head, the caller decides the key. Shared by the runner-scope
migration's tests and the runner transfer's."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from blizzard.runner.store import schema

T0 = datetime(2026, 1, 1, tzinfo=UTC)

#: Every runner-scoped table the additive revision keys, but the identity row, which keys itself by its own
#: ``runner_id``, the outbox counters, which only the tightening revision creates, and the tables a later revision
#: creates already keyed.
KEYED = frozenset(t.name for t in schema.metadata.sorted_tables if t.info["scope"] == "runner") - {
    "runner_identity",
    "outbox_counters",
    "runner_usage_limits",
    "runner_floor_clears",
    "fact_counters",
}

_ALLOWED_VALUES = re.compile(r"^\s*(\w+)\s+IN\s*\(\s*'([^']*)'")


def reflect(engine: sa.Engine) -> sa.MetaData:
    reflected = sa.MetaData()
    reflected.reflect(engine)
    return reflected


def _value_for(column: sa.Column[Any]) -> object:
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


def seed_every_keyed_table(
    engine: sa.Engine,
    shapes: sa.MetaData,
    *,
    omit: frozenset[str] = frozenset(),
    values: Mapping[str, object] | None = None,
) -> None:
    """One row in every keyed table, built from SHAPES: every column gets a value of its own type, or the first value
    its ``IN (...)`` CHECK constraint allows; a column named in ``omit`` is left out, to its default. The shapes name
    only the columns they reflected, so built from the parent's they write exactly as the previous release's statements
    do."""
    seed_tables(engine, shapes, KEYED, omit=omit, values=values)


def seed_tables(
    engine: sa.Engine,
    shapes: sa.MetaData,
    names: Iterable[str],
    *,
    omit: frozenset[str] = frozenset(),
    values: Mapping[str, object] | None = None,
) -> None:
    """One row in each of the tables ``names``, built as :func:`seed_every_keyed_table` builds one; a column named in
    ``values`` takes that value instead, in every table."""
    inspector = sa.inspect(engine)
    with engine.begin() as conn:
        for name in sorted(names):
            table = shapes.tables[name]
            allowed = {
                match.group(1): match.group(2)
                for check in inspector.get_check_constraints(name)
                if (match := _ALLOWED_VALUES.match(check["sqltext"])) is not None
            }
            row = {
                c.name: (values or {}).get(c.name, allowed.get(c.name, _value_for(c)))
                for c in table.columns
                if c.name not in omit
            }
            conn.execute(sa.insert(table).values(row))
