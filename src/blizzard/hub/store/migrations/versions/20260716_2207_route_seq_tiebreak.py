"""route-event monotonic tiebreak — ``seq`` on ``route_created``/``route_released``, with
a backfill assigning 1..n in timestamp order, ties created-before-released (issue #41)

Revision ID: 20260716_2207_hub_route_seq_tiebreak
Revises: 20260716_2206_hub_pr_opened_idempotent
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260716_2207_hub_route_seq_tiebreak"
down_revision: str | None = "20260716_2206_hub_pr_opened_idempotent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROUTE_CREATED = sa.Table(
    "route_created",
    sa.MetaData(),
    sa.Column("route_id", sa.String, primary_key=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("seq", sa.Integer, nullable=True),
)

_ROUTE_RELEASED = sa.Table(
    "route_released",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("released_at", UtcDateTime, nullable=False),
    sa.Column("seq", sa.Integer, nullable=True),
)

# A created row sorts before a released row on a same-instant tie (see the module
# docstring's backfill note) — the sort key's second element.
_CREATED, _RELEASED = 0, 1


def _has_seq(bind: sa.Connection, table: str) -> bool:
    return "seq" in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if _has_seq(bind, "route_created"):
        return  # already reshaped — this revision's own guard, not per-row

    with op.batch_alter_table("route_created") as batch:
        batch.add_column(sa.Column("seq", sa.Integer, nullable=True))
    with op.batch_alter_table("route_released") as batch:
        batch.add_column(sa.Column("seq", sa.Integer, nullable=True))

    created = bind.execute(sa.select(_ROUTE_CREATED)).all()
    released = bind.execute(sa.select(_ROUTE_RELEASED)).all()

    events: dict[str, list[tuple[datetime, int, object]]] = defaultdict(list)
    for row in created:
        events[row.chunk_id].append((row.created_at, _CREATED, row.route_id))
    for row in released:
        events[row.chunk_id].append((row.released_at, _RELEASED, row.id))

    for chunk_events in events.values():
        chunk_events.sort(key=lambda e: (e[0], e[1]))
        for seq, (_, kind, key) in enumerate(chunk_events, start=1):
            if kind == _CREATED:
                bind.execute(_ROUTE_CREATED.update().where(_ROUTE_CREATED.c.route_id == key).values(seq=seq))
            else:
                bind.execute(_ROUTE_RELEASED.update().where(_ROUTE_RELEASED.c.id == key).values(seq=seq))

    with op.batch_alter_table("route_created") as batch:
        batch.alter_column("seq", nullable=False)
    with op.batch_alter_table("route_released") as batch:
        batch.alter_column("seq", nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_seq(bind, "route_created"):
        return  # already the pre-0014 shape

    with op.batch_alter_table("route_created") as batch:
        batch.drop_column("seq")
    with op.batch_alter_table("route_released") as batch:
        batch.drop_column("seq")
