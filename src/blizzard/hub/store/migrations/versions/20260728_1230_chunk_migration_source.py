"""``chunk_migrations.source`` — what moved the chunk; additive and nullable with no
backfill, so a legacy row's provenance stays unrecorded rather than fabricated (#164)

Revision ID: 20260728_1230_hub_chunk_migration_source
Revises: 20260728_1200_hub_graph_policy_facts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_1230_hub_chunk_migration_source"
down_revision: str | None = "20260728_1200_hub_graph_policy_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "chunk_migrations"
_COLUMN = "source"


def _columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _columns(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _columns(bind):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)
