"""Add the transcript invocation-boundary table (blizzard#437 D6/D11).

Revision ID: 20260918_1000_invocation_boundaries
Revises: 20260917_1200_elicitation_process_group
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision = "20260918_1000_invocation_boundaries"
down_revision = "20260917_1200_elicitation_process_group"
branch_labels = None
depends_on = None

_TABLE = "invocation_boundaries"


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("lease_id", sa.String(), nullable=False),
        sa.Column("chunk_id", sa.String(), nullable=False),
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("start_position", sa.String(), nullable=True),
        sa.Column("opened_at", UtcDateTime(), nullable=False),
        sa.Column("closed_at", UtcDateTime(), nullable=True),
        sa.Column("closed_reason", sa.String(), nullable=True),
    )
    op.create_index("ix_invocation_boundaries_lease_id", _TABLE, ["lease_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLE in sa.inspect(bind).get_table_names():
        op.drop_table(_TABLE)
