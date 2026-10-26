"""fact append sequence — a nullable ``seq`` on ``env_bindings``, ``binding_releases``, ``pause_parks``,
``pause_park_resumes``, ``requeues`` and ``leases``, numbered per runner from the new ``fact_counters`` table, so a
release closes a bind, a resume closes a park and a lease mint consumes a requeue by append order rather than by
comparing instants.

The upgrade numbers every existing row of each runner in one order: by instant, an opener (bind, park, requeue) before
a closer (release, resume, lease) at a tie, then by row key. A closer then has the greater ``seq`` exactly where its
instant was at or after its opener's, so no stream open or closed before the upgrade changes. Each runner's counter
starts at its greatest ``seq``. One transaction; a runner whose rows all carry a ``seq`` is skipped, so a rerun changes
nothing. ``downgrade()`` drops the columns and the table; every row is kept.

Revision ID: 20261026_1000_fact_append_sequence
Revises: 20261023_1000_pause_park_cause
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20261026_1000_fact_append_sequence"
down_revision: str | None = "20261023_1000_pause_park_cause"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COUNTERS = "fact_counters"
_COLUMN = "seq"

#: Each sequenced table with the column naming its row's instant and the one naming its row, opener tables first: at
#: one instant an opener is numbered before a closer.
_OPENERS: tuple[tuple[str, str, str], ...] = (
    ("env_bindings", "bound_at", "id"),
    ("pause_parks", "parked_at", "id"),
    ("requeues", "requeued_at", "id"),
)
_CLOSERS: tuple[tuple[str, str, str], ...] = (
    ("binding_releases", "released_at", "id"),
    ("pause_park_resumes", "resumed_at", "id"),
    ("leases", "created_at", "lease_id"),
)
_TABLES = tuple(name for name, _, _ in (*_OPENERS, *_CLOSERS))
_ROW_KEY = {name: key for name, _, key in (*_OPENERS, *_CLOSERS)}


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if _COUNTERS not in sa.inspect(bind).get_table_names():
        op.create_table(
            _COUNTERS,
            sa.Column("runner_id", sa.String(), primary_key=True),
            sa.Column("last_seq", sa.Integer(), nullable=False),
        )
    for table in _TABLES:
        if _COLUMN not in _columns(bind, table):
            op.add_column(table, sa.Column(_COLUMN, sa.Integer(), nullable=True))
    _backfill(bind)


def _backfill(bind: sa.Connection) -> None:
    unnumbered = {
        runner_id
        for table in _TABLES
        for runner_id in bind.execute(
            sa.text(f"SELECT DISTINCT runner_id FROM {table} WHERE {_COLUMN} IS NULL")
        ).scalars()
    }
    for runner_id in sorted(unnumbered):
        facts: list[tuple[Any, int, str, Any]] = []
        for rank, group in enumerate((_OPENERS, _CLOSERS)):
            for table, instant, key in group:
                rows = bind.execute(
                    sa.text(f"SELECT {instant}, {key} FROM {table} WHERE runner_id = :runner"), {"runner": runner_id}
                )
                facts.extend((at, rank, table, row_key) for at, row_key in rows)
        facts.sort()
        for seq, (_, _, table, row_key) in enumerate(facts, start=1):
            bind.execute(
                sa.text(f"UPDATE {table} SET {_COLUMN} = :seq WHERE runner_id = :runner AND {_ROW_KEY[table]} = :key"),
                {"seq": seq, "runner": runner_id, "key": row_key},
            )
        _seed_counter(bind, runner_id, len(facts))


def _seed_counter(bind: sa.Connection, runner_id: str, last_seq: int) -> None:
    params = {"runner": runner_id, "last": last_seq}
    if bind.execute(sa.text(f"SELECT 1 FROM {_COUNTERS} WHERE runner_id = :runner"), params).first() is None:
        bind.execute(sa.text(f"INSERT INTO {_COUNTERS} (runner_id, last_seq) VALUES (:runner, :last)"), params)
    else:
        bind.execute(
            sa.text(f"UPDATE {_COUNTERS} SET last_seq = :last WHERE runner_id = :runner AND last_seq < :last"), params
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        if _COLUMN in _columns(bind, table):
            with op.batch_alter_table(table) as batch:
                batch.drop_column(_COLUMN)
    if _COUNTERS in sa.inspect(bind).get_table_names():
        op.drop_table(_COUNTERS)
