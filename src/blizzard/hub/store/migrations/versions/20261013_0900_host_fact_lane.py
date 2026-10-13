"""host fact lane — a host's own high-water mark and its subscription usage samples and misses.

Adds three tenant-keyed tables; downgrade drops them.
Revision ID: 20261013_0900_host_fact_lane
Revises: 20261012_1500_work_source_identity
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261013_0900_host_fact_lane"
down_revision: str | None = "20261012_1500_work_source_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_COLUMN = "tenant_id"
_MARK = "host_high_water"
_USAGE = "host_external_usage"
_MISSES = "host_external_usage_misses"
#: In the order they are created; dropped in reverse.
_NEW_TABLES = (_MARK, _USAGE, _MISSES)


def _has_table(bind: sa.Connection, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def _index_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(i["name"]) for i in sa.inspect(bind).get_indexes(table)}


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([_TENANT_COLUMN], ["tenants.tenant_id"], name=f"fk_{table}_tenant_id")


def _create_mark() -> None:
    op.create_table(
        _MARK,
        sa.Column("host_id", sa.String(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.Column(_TENANT_COLUMN, sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("host_id", name=f"pk_{_MARK}"),
        _tenant_fk(_MARK),
    )


def _create_usage() -> None:
    op.create_table(
        _USAGE,
        sa.Column("host_id", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("sampled_at", UtcDateTime(), nullable=False),
        sa.Column("windows", sa.Text(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.Column(_TENANT_COLUMN, sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("host_id", "slug", name=f"pk_{_USAGE}"),
        _tenant_fk(_USAGE),
    )


def _create_misses() -> None:
    op.create_table(
        _MISSES,
        sa.Column("host_id", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("missed_at", UtcDateTime(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.Column(_TENANT_COLUMN, sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("host_id", "slug", name=f"pk_{_MISSES}"),
        _tenant_fk(_MISSES),
    )


def upgrade() -> None:
    bind = op.get_bind()
    for table, create in ((_MARK, _create_mark), (_USAGE, _create_usage), (_MISSES, _create_misses)):
        if not _has_table(bind, table):
            create()
        if f"ix_{table}_tenant_id" not in _index_names(bind, table):
            op.create_index(f"ix_{table}_tenant_id", table, [_TENANT_COLUMN])


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_NEW_TABLES):
        if _has_table(bind, table):
            op.drop_table(table)
