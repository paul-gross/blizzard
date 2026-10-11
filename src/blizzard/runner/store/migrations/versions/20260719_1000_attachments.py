"""worker attachment channel — attachments (runner store tree)

Append-only, one row per attach call, latest-wins per ``(lease_id, name)``; ``checkfirst``.
Revision ID: 20260719_1000_runner_attachments
Revises: 20260719_0900_runner_lease_tokens
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime


revision: str = "20260719_1000_runner_attachments"
down_revision: str | None = "20260719_0900_runner_lease_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_frozen_metadata = sa.MetaData()
_attachments = sa.Table(
    "attachments",
    _frozen_metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("lease_id", sa.String(), nullable=False),
    sa.Column("chunk_id", sa.String(), nullable=False),
    sa.Column("node_id", sa.String(), nullable=False),
    sa.Column("epoch", sa.Integer(), nullable=False),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("content", sa.Text(), nullable=False),
    sa.Column("attached_at", UtcDateTime(), nullable=False),
)

_TABLES = (_attachments,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
