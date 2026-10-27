"""chunk_demoted — the append-only fact that moves a promoted chunk back to ``not_ready``.

Adds the tenant-keyed ``chunk_demoted`` table with its per-chunk and newest-first indexes. It starts empty, so no
existing row changes.
Each step inspects first and skips what a stopped run already did.

The downgrade drops the indexes and the table.
Revision ID: 20261016_0900_chunk_demoted
Revises: 20261014_0900_question_decision_project
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261016_0900_chunk_demoted"
down_revision: str | None = "20261014_0900_question_decision_project"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "chunk_demoted"
_TENANT_COLUMN = "tenant_id"

#: Indexes this revision adds: name, columns.
_ADDED_INDEXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ix_chunk_demoted_tenant_chunk_id", (_TENANT_COLUMN, "chunk_id")),
    ("ix_chunk_demoted_tenant_demoted_at_id", (_TENANT_COLUMN, "demoted_at", "id")),
)


def _has_table(bind: sa.Connection) -> bool:
    return sa.inspect(bind).has_table(_TABLE)


def _index_names(bind: sa.Connection) -> set[str]:
    return {str(i["name"]) for i in sa.inspect(bind).get_indexes(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("chunk_id", sa.String(), nullable=False),
            sa.Column("demoted_at", UtcDateTime(), nullable=False),
            sa.Column(_TENANT_COLUMN, sa.String(), nullable=False),
            sa.ForeignKeyConstraint(["chunk_id"], ["chunks.chunk_id"]),
            sa.ForeignKeyConstraint([_TENANT_COLUMN], ["tenants.tenant_id"], name=f"fk_{_TABLE}_tenant_id"),
        )
    existing = _index_names(bind)
    for name, columns in _ADDED_INDEXES:
        if name not in existing:
            op.create_index(name, _TABLE, list(columns))


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    existing = _index_names(bind)
    for name, _ in reversed(_ADDED_INDEXES):
        if name in existing:
            op.drop_index(name, table_name=_TABLE)
    op.drop_table(_TABLE)
