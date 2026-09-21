"""Add the provider-overload backoff fact tables (blizzard#595).

Revision ID: 20260921_1600_overload_facts
Revises: 20260921_1000_local_pause_reason
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision = "20260921_1600_overload_facts"
down_revision = "20260921_1000_local_pause_reason"
branch_labels = None
depends_on = None

_FACTS = "overload_facts"
_RESETS = "overload_resets"


def upgrade() -> None:
    bind = op.get_bind()
    existing = sa.inspect(bind).get_table_names()
    if _FACTS not in existing:
        op.create_table(
            _FACTS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("lease_id", sa.String(), nullable=False),
            sa.Column("chunk_id", sa.String(), nullable=False),
            sa.Column("epoch", sa.Integer(), nullable=False),
            sa.Column("generation", sa.Integer(), nullable=False),
            sa.Column("invocation_kind", sa.String(), nullable=False),
            sa.Column("invocation_identity", sa.String(), nullable=False),
            sa.Column("streak_ordinal", sa.Integer(), nullable=False),
            sa.Column("observed_at", UtcDateTime(), nullable=False),
            sa.Column("resume_after", UtcDateTime(), nullable=True),
        )
    if _RESETS not in existing:
        op.create_table(
            _RESETS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("lease_id", sa.String(), nullable=False),
            sa.Column("epoch", sa.Integer(), nullable=False),
            sa.Column("reset_at", UtcDateTime(), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = sa.inspect(bind).get_table_names()
    if _RESETS in existing:
        op.drop_table(_RESETS)
    if _FACTS in existing:
        op.drop_table(_FACTS)
