"""usage-limit floor — four new empty tables: the host-scoped ``subscription_usage_limits`` and
``subscription_floor_clears`` and their runner-scoped twins; a rerun skips a table already present.

Revision ID: 20261022_1000_usage_limit_floor
Revises: 20261019_1000_served_project_color
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261022_1000_usage_limit_floor"
down_revision: str | None = "20261019_1000_served_project_color"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Each table's key column — the subscription's slug on the host, the runner on its twin.
_MARKS = {"subscription_usage_limits": "slug", "runner_usage_limits": "runner_id"}
_CLEARS = {"subscription_floor_clears": "slug", "runner_floor_clears": "runner_id"}


def _mark_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("marked_at", UtcDateTime(), nullable=False),
        sa.Column("resets_at", UtcDateTime(), nullable=True),
        sa.Column("window", sa.String(), nullable=True),
        sa.Column("window_resets_at", UtcDateTime(), nullable=True),
        sa.Column("harness_id", sa.String(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("lease_id", sa.String(), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("invocation_kind", sa.String(), nullable=False),
    ]


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for table, key in _MARKS.items():
        if table in existing:
            continue
        # The host's marks are unique on the invocation alone; a runner's lead with the runner they belong to.
        invocation = ("lease_id", "epoch", "generation", "invocation_kind")
        unique = invocation if key == "slug" else (key, *invocation)
        op.create_table(
            table,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(key, sa.String(), nullable=False),
            *_mark_columns(),
            sa.UniqueConstraint(*unique, name=f"uq_{table}_invocation"),
            sa.CheckConstraint("invocation_kind IN ('worker', 'judge')", name=f"ck_{table}_invocation_kind"),
        )
        op.create_index(f"ix_{table}_{key}_marked_at", table, [key, "marked_at"])
    for table, key in _CLEARS.items():
        if table in existing:
            continue
        op.create_table(
            table,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(key, sa.String(), nullable=False),
            sa.Column("cleared_at", UtcDateTime(), nullable=False),
            sa.Column("by", sa.String(), nullable=False),
        )
        op.create_index(f"ix_{table}_{key}_cleared_at", table, [key, "cleared_at"])


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for table, key in {**_MARKS, **_CLEARS}.items():
        if table in existing:
            column = "marked_at" if table in _MARKS else "cleared_at"
            op.drop_index(f"ix_{table}_{key}_{column}", table_name=table)
            op.drop_table(table)
